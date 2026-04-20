"""
City seeder — discovers Instagram anchor handles for a city using Claude.

For each category, asks Claude for up to 20 boutique/local businesses in the city
with confident Instagram handles. Validates each handle exists (HTTP HEAD),
verifies the account has 3k–30k followers via Apify, then inserts into
`anchor_locations`. Chains are excluded via the prompt and the 30k follower cap.
"""

from __future__ import annotations

import json
import os
import re

import requests
from anthropic import Anthropic

from scraper import _supabase_client, scrape_profile, ProfileNotFoundError


CATEGORIES = [
    "med spas",
    "nail parlors",
    "hair salons",
    "pilates studios",
    "yoga studios",
]

MIN_FOLLOWERS = 3000
MAX_FOLLOWERS = 30000

SYSTEM_PROMPT = (
    "Return a JSON array of objects with keys 'business_name' and 'instagram_handle'. "
    "The instagram_handle must be the exact IG username — no URL, no '@', no spaces. "
    "Only include businesses where you are confident about the Instagram handle. "
    "Return ONLY valid JSON, no prose."
)


def _anthropic_client() -> Anthropic:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY must be set in .env")
    return Anthropic(api_key=key)


def _query_claude_for_category(
    client: Anthropic, city_name: str, state: str, category: str, max_results: int = 20
) -> list[dict]:
    """Ask Claude for up to `max_results` anchor candidates in one category."""
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

    user_msg = (
        f"What are the best boutique womens' {category} in {city_name}, {state}? "
        f"Return up to {max_results} results with their Instagram handles as a JSON array."
    )

    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    ).strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try to find a JSON array inside the text
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            print(f"  ⚠ Claude returned unparseable output for {category}: {raw[:200]}")
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            print(f"  ⚠ Could not parse JSON array for {category}")
            return []

    if not isinstance(data, list):
        return []

    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        handle = (item.get("instagram_handle") or "").strip().lstrip("@").rstrip("/")
        name = (item.get("business_name") or "").strip()
        if handle and re.match(r"^[A-Za-z0-9_.]+$", handle):
            out.append({"business_name": name, "instagram_handle": handle})
    return out


def _validate_handle_exists(handle: str) -> bool:
    """Lightweight HEAD request to verify the IG handle resolves (200)."""
    try:
        url = f"https://www.instagram.com/{handle}/"
        r = requests.head(url, timeout=10, allow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"})
        # IG sometimes returns 200 even for nonexistent, but 404 is a clear no.
        return r.status_code == 200
    except requests.RequestException:
        return False


def _check_follower_count(handle: str) -> int | None:
    """Use Apify profile scrape to get follower count. Paid but one-time per anchor candidate."""
    try:
        profile, _ = scrape_profile(handle)
        return profile.get("followers")
    except ProfileNotFoundError:
        return None
    except Exception as e:
        print(f"  ⚠ follower count check failed for @{handle}: {e}")
        return None


def seed_city(city_id: str, on_progress=None, max_anchors: int | None = None) -> dict:
    """Run the full seeding pipeline for a city. Updates the `cities` row to 'active' on success.

    Args:
        city_id: UUID of the cities row to seed.
        on_progress: optional callback invoked with progress messages.
        max_anchors: if set, stop as soon as this many eligible `pending` anchors have
            been inserted. Used for cheap testing. Invalid/out-of-range candidates do
            not count toward this limit.
    """
    def emit(msg: str, **kwargs):
        if on_progress:
            try:
                on_progress({"message": msg, **kwargs})
            except Exception:
                pass
        print(msg)

    supabase = _supabase_client()

    city = (
        supabase.table("cities").select("*").eq("id", city_id).limit(1).execute().data
    )
    if not city:
        raise RuntimeError(f"City {city_id} not found")
    city = city[0]

    emit(f"Seeding anchors for {city['name']}, {city['state']} …")

    client = _anthropic_client()

    stats = {"candidates": 0, "invalid_handle": 0, "wrong_follower_count": 0,
             "inserted": 0, "errors": 0}

    for category in CATEGORIES:
        if max_anchors is not None and stats["inserted"] >= max_anchors:
            emit(f"  Reached max_anchors={max_anchors}, stopping.")
            break
        emit(f"  Category: {category}", category=category)
        try:
            candidates = _query_claude_for_category(client, city["name"], city["state"], category)
        except Exception as e:
            emit(f"    ⚠ Claude query failed: {e}")
            stats["errors"] += 1
            continue

        emit(f"    Claude returned {len(candidates)} candidates")
        stats["candidates"] += len(candidates)

        for c in candidates:
            if max_anchors is not None and stats["inserted"] >= max_anchors:
                break
            handle = c["instagram_handle"]

            # Skip if we already have this anchor in this city
            existing = (
                supabase.table("anchor_locations")
                .select("id")
                .eq("city_id", city_id)
                .eq("username", handle)
                .limit(1)
                .execute()
                .data
            )
            if existing:
                continue

            if not _validate_handle_exists(handle):
                emit(f"    ✗ @{handle} — handle doesn't resolve (skipped)")
                stats["invalid_handle"] += 1
                continue

            followers = _check_follower_count(handle)
            if followers is None:
                emit(f"    ✗ @{handle} — could not fetch profile (skipped)")
                stats["errors"] += 1
                continue

            if followers < MIN_FOLLOWERS or followers > MAX_FOLLOWERS:
                emit(f"    ✗ @{handle} — {followers:,} followers (out of 3k–30k, skipped)")
                stats["wrong_follower_count"] += 1
                continue

            # Eligible! Insert as pending.
            supabase.table("anchor_locations").upsert({
                "city_id": city_id,
                "username": handle,
                "display_name": c["business_name"],
                "category": category,
                "follower_count": followers,
                "status": "pending",
            }, on_conflict="city_id,username").execute()
            emit(f"    ✓ @{handle} — {followers:,} followers ({category})")
            stats["inserted"] += 1

    supabase.table("cities").update({"status": "active"}).eq("id", city_id).execute()
    emit(f"✓ Seeding complete. Stats: {stats}")
    return stats
