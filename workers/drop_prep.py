"""
Drop prep — the just-in-time harvesting worker.

Given the set of users who are eligible for the next drop, compute per-user
shortfalls (how many fresh qualified profiles they're missing vs the target of 10).
Harvest only enough anchor followers to close those shortfalls. If a user's
home city is exhausted for them, expand outward by distance to other cities
(seeding new cities on demand). No radius cap — every eligible user gets 10.

Between drops this does nothing, so $0 spend when idle.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from scraper import (
    _supabase_client,
    analyze_and_save_follower,
    fetch_followers,
)

TARGET_RECOMMENDATIONS = 10
FETCH_FOLLOWERS_MAX = 5000
BETWEEN_FOLLOWERS_SLEEP = 0  # worker respects existing SCAN_DELAY via Apify/Grok latency


def _log(emit, msg: str, **kwargs):
    if emit:
        try:
            emit({"message": msg, **kwargs})
        except Exception:
            pass
    print(msg)


def _eligible_users(supabase) -> list[dict]:
    """Users whose first_eligible_drop_at has arrived (ignoring any drop already delivered today)."""
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    res = (
        supabase.table("users")
        .select("id, city_id, lat, lng, first_eligible_drop_at, email, name")
        .lte("first_eligible_drop_at", now_iso)
        .not_.is_("city_id", "null")
        .execute()
    )
    return res.data or []


def _count_available_for_user(supabase, user: dict) -> int:
    """Dry-run curate — returns how many profiles are currently available for this user.

    Uses the SQL function curate_drop_for_user with p_dry_run=true. The function
    inserts nothing in dry-run mode; we just count rows.
    """
    res = supabase.rpc(
        "curate_drop_for_user",
        {"p_user_id": user["id"], "p_drop_date": _today_iso(), "p_dry_run": True},
    ).execute()
    rows = res.data or []
    return len(rows)


def _today_iso() -> str:
    from datetime import date
    return date.today().isoformat()


def _nearest_city_for_user(supabase, user: dict, exclude_city_ids: list[str]) -> Optional[dict]:
    """Find the city (excluding given list) with known lat/lng closest to the user.

    Returns the city row or None if none qualify.
    """
    # Simple approach: fetch all cities with coords and compute in Python.
    res = supabase.table("cities").select("id, name, state, lat, lng, status").execute()
    cities = [c for c in (res.data or []) if c.get("lat") is not None and c.get("lng") is not None]
    if user.get("lat") is None or user.get("lng") is None:
        return None

    def dist(c):
        from math import radians, cos, sin, asin, sqrt
        lat1, lng1 = float(user["lat"]), float(user["lng"])
        lat2, lng2 = float(c["lat"]), float(c["lng"])
        dlat = radians(lat2 - lat1)
        dlng = radians(lng2 - lng1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
        return 3958.8 * 2 * asin(sqrt(a))

    candidates = [c for c in cities if c["id"] not in exclude_city_ids]
    if not candidates:
        return None
    candidates.sort(key=dist)
    return candidates[0]


def _next_pending_anchor(supabase, city_id: str) -> Optional[dict]:
    res = (
        supabase.table("anchor_locations")
        .select("*")
        .eq("city_id", city_id)
        .eq("status", "pending")
        .order("follower_count", desc=True)
        .limit(1)
        .execute()
    )
    return (res.data or [None])[0]


def _harvest_one_anchor(supabase, anchor: dict, emit=None) -> int:
    """Fetch followers for one anchor, skip already-seen usernames, enqueue the rest.

    Returns the number of new usernames enqueued (a rough upper bound on upcoming spend).
    """
    supabase.table("anchor_locations").update({"status": "in_progress"}).eq("id", anchor["id"]).execute()

    try:
        followers = fetch_followers(anchor["username"], fetch_count=FETCH_FOLLOWERS_MAX)
    except Exception as e:
        _log(emit, f"  ⚠ fetch_followers failed for @{anchor['username']}: {e}")
        supabase.table("anchor_locations").update({
            "status": "invalid",
            "invalid_reason": f"fetch failed: {e}",
        }).eq("id", anchor["id"]).execute()
        return 0

    enqueued = 0
    for f in followers:
        # Dedupe against profiles (qualified or disqualified)
        exists = (
            supabase.table("profiles")
            .select("username")
            .eq("username", f)
            .limit(1)
            .execute()
            .data
        )
        if exists:
            continue

        # Don't double-queue
        pending = (
            supabase.table("analysis_queue")
            .select("id")
            .eq("follower_username", f)
            .in_("status", ["queued", "in_progress"])
            .limit(1)
            .execute()
            .data
        )
        if pending:
            continue

        supabase.table("analysis_queue").insert({
            "follower_username": f,
            "city_id": anchor["city_id"],
            "anchor_id": anchor["id"],
            "status": "queued",
        }).execute()
        enqueued += 1

    from datetime import datetime, timezone
    supabase.table("anchor_locations").update({
        "status": "done",
        "followers_fetched_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", anchor["id"]).execute()

    _log(emit, f"  Anchor @{anchor['username']} → {len(followers)} followers, {enqueued} new queued")
    return enqueued


def _drain_city_queue_until_gap_closes(
    supabase,
    city_id: str,
    eligible_users: list[dict],
    emit=None,
) -> int:
    """Pull rows from analysis_queue for this city, run analysis, stop when no user
    in this city has a gap (or queue is empty).

    Returns number of profiles analyzed.
    """
    analyzed = 0
    while True:
        # Check if ANY eligible user is still short
        any_gap = False
        for u in eligible_users:
            avail = _count_available_for_user(supabase, u)
            if avail < TARGET_RECOMMENDATIONS:
                any_gap = True
                break
        if not any_gap:
            _log(emit, "  ✓ All users covered — stopping analysis")
            return analyzed

        # Grab next queued row for this city
        res = (
            supabase.table("analysis_queue")
            .select("*")
            .eq("city_id", city_id)
            .eq("status", "queued")
            .order("id")
            .limit(1)
            .execute()
        )
        row = (res.data or [None])[0]
        if row is None:
            return analyzed  # no more work in this city

        from datetime import datetime, timezone
        supabase.table("analysis_queue").update({
            "status": "in_progress",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", row["id"]).execute()

        try:
            result = analyze_and_save_follower(
                row["follower_username"],
                city_id=row["city_id"],
                anchor_id=row.get("anchor_id"),
            )
            supabase.table("analysis_queue").update({
                "status": "done",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "last_error": None if result["status"] != "error" else result["reason"][:500],
            }).eq("id", row["id"]).execute()
            _log(emit,
                 f"  @{row['follower_username']} → {result['status']} ({result['reason'][:80] if result['reason'] else ''})")
            analyzed += 1
        except Exception as e:
            attempts = row.get("attempts", 0) + 1
            supabase.table("analysis_queue").update({
                "status": "failed" if attempts >= 3 else "queued",
                "attempts": attempts,
                "last_error": str(e)[:500],
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", row["id"]).execute()
            _log(emit, f"  ⚠ analysis failed for @{row['follower_username']}: {e}")


def _harvest_city_until_users_satisfied(
    supabase,
    city_id: str,
    users_in_city: list[dict],
    emit=None,
) -> int:
    """For a given city, harvest anchors and analyze followers until every user in
    `users_in_city` has ≥10 available. Returns total profiles analyzed.
    """
    total_analyzed = 0
    while True:
        # Check gap
        any_gap = False
        for u in users_in_city:
            if _count_available_for_user(supabase, u) < TARGET_RECOMMENDATIONS:
                any_gap = True
                break
        if not any_gap:
            return total_analyzed

        # Try to drain existing queue first
        drained = _drain_city_queue_until_gap_closes(supabase, city_id, users_in_city, emit=emit)
        total_analyzed += drained

        # Re-check gap after draining
        any_gap = False
        for u in users_in_city:
            if _count_available_for_user(supabase, u) < TARGET_RECOMMENDATIONS:
                any_gap = True
                break
        if not any_gap:
            return total_analyzed

        # Need more fuel — harvest a new anchor
        anchor = _next_pending_anchor(supabase, city_id)
        if anchor is None:
            _log(emit, f"  No more pending anchors in city {city_id}")
            return total_analyzed

        _harvest_one_anchor(supabase, anchor, emit=emit)
        # loop back to drain this new batch


def run_drop_prep(emit=None) -> dict:
    """Main entry point — run one full drop prep pass.

    Returns stats dict with per-step counters.
    """
    supabase = _supabase_client()
    stats = {"users": 0, "users_covered_initially": 0, "analyzed": 0,
             "anchors_harvested": 0, "cross_city_expansions": 0}

    users = _eligible_users(supabase)
    stats["users"] = len(users)
    if not users:
        _log(emit, "No eligible users. Drop prep exits immediately (no spend).")
        return stats

    _log(emit, f"Drop prep — {len(users)} eligible user(s)")

    # Group users by home city
    from collections import defaultdict
    users_by_city: dict[str, list[dict]] = defaultdict(list)
    for u in users:
        users_by_city[u["city_id"]].append(u)

    # Phase 1: home-city harvesting
    for city_id, city_users in users_by_city.items():
        # Is there already enough for everyone?
        gaps = [(u, _count_available_for_user(supabase, u)) for u in city_users]
        if all(g[1] >= TARGET_RECOMMENDATIONS for g in gaps):
            stats["users_covered_initially"] += len(city_users)
            _log(emit, f"  City {city_id}: all {len(city_users)} users already covered — skipping")
            continue

        _log(emit, f"  City {city_id}: gaps exist, harvesting home city first")

        # Seed the city if it's still seeding
        city_row = supabase.table("cities").select("*").eq("id", city_id).limit(1).execute().data
        if city_row and city_row[0]["status"] == "seeding":
            from workers.city_seeder import seed_city
            _log(emit, f"  City {city_id} is still seeding — running seeder now")
            seed_city(city_id, on_progress=emit)

        analyzed = _harvest_city_until_users_satisfied(supabase, city_id, city_users, emit=emit)
        stats["analyzed"] += analyzed

    # Phase 2: cross-city fallback — any user still short of 10?
    for u in users:
        exclude: list[str] = [u["city_id"]]
        while _count_available_for_user(supabase, u) < TARGET_RECOMMENDATIONS:
            _log(emit, f"  User {u.get('email') or u['id']} short of 10 — expanding cross-city")

            next_city = _nearest_city_for_user(supabase, u, exclude_city_ids=exclude)
            if next_city is None:
                _log(emit, "  ⚠ No more cities to expand to — user will receive a partial drop")
                break

            exclude.append(next_city["id"])
            stats["cross_city_expansions"] += 1

            # Seed if needed
            if next_city["status"] == "seeding":
                from workers.city_seeder import seed_city
                _log(emit, f"  Seeding next-nearest city {next_city['name']}, {next_city['state']}")
                seed_city(next_city["id"], on_progress=emit)

            analyzed = _harvest_city_until_users_satisfied(supabase, next_city["id"], [u], emit=emit)
            stats["analyzed"] += analyzed

            if analyzed == 0:
                # No anchors left in this city either — keep expanding
                continue

    _log(emit, f"Drop prep complete. Stats: {stats}")
    return stats


def run_drop_execution(emit=None) -> dict:
    """Actually deliver drops — runs `curate_drop_for_user` (non-dry-run) for each eligible user."""
    from datetime import date
    supabase = _supabase_client()
    users = _eligible_users(supabase)
    today = date.today().isoformat()

    stats = {"users": len(users), "delivered": 0, "partial": 0}
    for u in users:
        res = supabase.rpc(
            "curate_drop_for_user",
            {"p_user_id": u["id"], "p_drop_date": today, "p_dry_run": False},
        ).execute()
        n = len(res.data or [])
        if n >= TARGET_RECOMMENDATIONS:
            stats["delivered"] += 1
        else:
            stats["partial"] += 1
        _log(emit, f"  {u.get('email') or u['id']} → {n} recommendations")

    return stats


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "prep"
    if cmd == "prep":
        run_drop_prep(emit=lambda e: None)
    elif cmd == "drop":
        run_drop_execution(emit=lambda e: None)
    else:
        print(f"Unknown command: {cmd}. Use 'prep' or 'drop'.")
