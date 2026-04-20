"""
Social Scout — FastAPI Backend
===============================
Thin API layer that imports from scraper.py.
Run with:  uvicorn server:app --reload --port 8000
"""

import os
import uuid
import queue
import threading
import time
import asyncio
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

from scraper import (
    extract_username,
    scrape_profile,
    calculate_engagement,
    analyze_profile_pic,
    write_to_supabase,
    fetch_followers,
    load_processed,
    mark_processed,
    precheck_username,
    precheck_bio,
    precheck_profile_pic,
    qualify_follower_basic,
    qualify_follower_vision,
    upload_profile_pic,
    get_instagram_pic_url,
    get_storage_pic_url,
    ProfileNotFoundError,
    _supabase_client,
)

app = FastAPI(title="Social Scout API")


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------

class ScrapeRequest(BaseModel):
    username: str
    skip_vision: bool = False
    skip_engagement: bool = False
    only_fields: Optional[list[str]] = None
    location_id: Optional[str] = None
    location_slug: Optional[str] = None


class ScanRequest(BaseModel):
    username: str
    max_followers: Optional[int] = None
    location_id: Optional[str] = None
    location_slug: Optional[str] = None


# ---------------------------------------------------------------------------
# In-memory scan state
# ---------------------------------------------------------------------------

# Maps scan_id -> { "queue": Queue, "thread": Thread, "stop": Event }
active_scans: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# POST /api/scrape  —  Single profile scrape
# ---------------------------------------------------------------------------

@app.post("/api/scrape")
def scrape_single(req: ScrapeRequest):
    username = extract_username(req.username)

    # Build location data
    location_data = {}
    if req.location_id:
        location_data["location_cluster_id"] = req.location_id
    if req.location_slug:
        location_data["location_slug"] = req.location_slug

    # Determine what to run
    only_fields = set(req.only_fields) if req.only_fields else None
    engagement_fields = {"avg_likes", "avg_comments", "engagement_rate"}

    if only_fields and only_fields & engagement_fields:
        only_fields |= engagement_fields

    run_engagement = not req.skip_engagement
    run_vision = not req.skip_vision

    if only_fields:
        run_vision = False
        run_engagement = bool(only_fields & engagement_fields)

    # Step 1: Scrape profile
    try:
        profile_data, raw_apify_data = scrape_profile(username)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scrape failed: {e}")

    # Filter fields if --only
    apify_fields = {
        "username", "name", "followers", "following",
        "bio", "is_private", "post_count",
    }
    if only_fields:
        apify_requested = only_fields & apify_fields
        apify_requested.add("username")
        profile_data = {k: v for k, v in profile_data.items() if k in apify_requested}

    # Step 2: Engagement
    engagement_data = {}
    if run_engagement:
        try:
            engagement_data = calculate_engagement(
                raw_apify_data, raw_apify_data.get("followersCount", 0)
            )
        except Exception:
            engagement_data = {"avg_likes": None, "avg_comments": None, "engagement_rate": None}

    # Step 3: Vision + profile pic upload
    vision_data = {}
    pic_url = get_instagram_pic_url(raw_apify_data)
    if run_vision and pic_url:
        vision_data = analyze_profile_pic(pic_url)

    # Upload profile picture to Supabase Storage
    if pic_url:
        upload_profile_pic(username, pic_url)

    # Step 4: Write to Supabase
    merged = {**profile_data, **engagement_data, **vision_data}
    if location_data:
        merged.update(location_data)

    try:
        write_to_supabase(merged)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Supabase write failed: {e}")

    # Inject storage pic URL for frontend display
    merged["profile_image_url"] = get_storage_pic_url(username)

    return {"status": "ok", "data": merged}


# ---------------------------------------------------------------------------
# POST /api/scan  —  Start follower scan
# ---------------------------------------------------------------------------

@app.post("/api/scan")
def start_scan(req: ScanRequest):
    username = extract_username(req.username)
    scan_id = str(uuid.uuid4())

    location_data = {}
    if req.location_id:
        location_data["location_cluster_id"] = req.location_id
    if req.location_slug:
        location_data["location_slug"] = req.location_slug

    q = queue.Queue()
    stop_event = threading.Event()

    thread = threading.Thread(
        target=_run_scan,
        args=(scan_id, username, req.max_followers, location_data, q, stop_event),
        daemon=True,
    )

    active_scans[scan_id] = {"queue": q, "thread": thread, "stop": stop_event}
    thread.start()

    return {"scan_id": scan_id, "username": username}


def _run_scan(
    scan_id: str,
    username: str,
    max_followers: Optional[int],
    location_data: dict,
    q: queue.Queue,
    stop_event: threading.Event,
):
    """Run the follower scan pipeline in a background thread, pushing events to the queue."""
    SCAN_DELAY_SECONDS = 2

    try:
        # Load already-processed
        already_processed = load_processed(username)
        q.put({"event": "info", "message": f"Resuming — {len(already_processed)} already processed"})

        # Fetch followers
        q.put({"event": "status", "message": "Fetching followers list..."})
        if max_followers:
            fetch_count = len(already_processed) + max_followers
        else:
            fetch_count = 100000

        try:
            follower_usernames = fetch_followers(username, fetch_count=fetch_count)
        except Exception as e:
            q.put({"event": "error", "message": f"Failed to fetch followers: {e}"})
            q.put({"event": "complete", "stats": {"qualified": 0, "disqualified": 0, "errors": 1}})
            return

        total_fetched = len(follower_usernames)
        remaining = [u for u in follower_usernames if u not in already_processed]

        q.put({"event": "info", "message": f"Fetched {total_fetched} followers, {len(remaining)} remaining after filtering processed"})

        if max_followers and len(remaining) > max_followers:
            remaining = remaining[:max_followers]

        total = len(remaining)
        if total == 0:
            q.put({"event": "complete", "stats": {"qualified": 0, "disqualified": 0, "errors": 0}, "message": "All followers already processed"})
            return

        q.put({"event": "scan_started", "total": total})

        stats = {"qualified": 0, "disqualified": 0, "errors": 0}

        for i, follower in enumerate(remaining, 1):
            if stop_event.is_set():
                q.put({"event": "info", "message": "Scan stopped by user"})
                break

            q.put({"event": "follower_start", "current": i, "total": total, "username": follower})

            try:
                result = _process_single_follower_for_scan(username, follower, stats, location_data, q)
            except ProfileNotFoundError as e:
                q.put({"event": "follower_result", "current": i, "total": total, "username": follower, "status": "error", "reason": str(e)})
                mark_processed(username, follower)
                stats["errors"] += 1
            except Exception as e:
                q.put({"event": "follower_result", "current": i, "total": total, "username": follower, "status": "error", "reason": f"{type(e).__name__}: {e}"})
                stats["errors"] += 1

            q.put({"event": "progress", "current": i, "total": total, "stats": dict(stats)})

            if i < total and not stop_event.is_set():
                time.sleep(SCAN_DELAY_SECONDS)

        q.put({"event": "complete", "stats": dict(stats)})

    except Exception as e:
        q.put({"event": "error", "message": f"Scan failed: {e}"})
        q.put({"event": "complete", "stats": {"qualified": 0, "disqualified": 0, "errors": 1}})
    finally:
        # Clean up after a delay to let SSE drain
        def cleanup():
            time.sleep(30)
            active_scans.pop(scan_id, None)
        threading.Thread(target=cleanup, daemon=True).start()


def _process_single_follower_for_scan(
    target_username: str,
    username: str,
    stats: dict,
    location_data: dict,
    q: queue.Queue,
):
    """Process a single follower, pushing step-by-step events to the queue."""

    # Step 0: Username precheck
    q.put({"event": "follower_step", "username": username, "step": "Username precheck"})
    if not precheck_username(username):
        q.put({"event": "follower_result", "username": username, "status": "disqualified", "reason": "username sounds like a business/organization"})
        stats["disqualified"] += 1
        mark_processed(target_username, username)
        return

    # Step 1: Scrape profile
    q.put({"event": "follower_step", "username": username, "step": "Scraping profile"})
    profile_data, raw_apify_data = scrape_profile(username)

    # Step 2: Basic qualification
    q.put({"event": "follower_step", "username": username, "step": "Basic qualification"})
    passed, reason = qualify_follower_basic(profile_data, raw_apify_data)
    if not passed:
        q.put({"event": "follower_result", "username": username, "status": "disqualified", "reason": reason})
        stats["disqualified"] += 1
        mark_processed(target_username, username)
        return

    # Step 2b: Follower ratio precheck
    follower_ratio = profile_data.get("follower_ratio")
    if follower_ratio is not None and follower_ratio < 1:
        q.put({"event": "follower_result", "username": username, "status": "disqualified", "reason": f"follower ratio too low ({follower_ratio})"})
        stats["disqualified"] += 1
        mark_processed(target_username, username)
        return

    # Step 2c: Engagement rate precheck (only for non-private accounts)
    if not profile_data.get("is_private", False):
        eng = calculate_engagement(raw_apify_data, profile_data.get("followers", 0))
        eng_rate = eng.get("engagement_rate")
        if eng_rate is not None and eng_rate < 5:
            q.put({"event": "follower_result", "username": username, "status": "disqualified", "reason": f"engagement rate too low ({eng_rate}%)"})
            stats["disqualified"] += 1
            mark_processed(target_username, username)
            return

    # Step 2c: Bio precheck
    bio = profile_data.get("bio") or ""
    if bio.strip():
        q.put({"event": "follower_step", "username": username, "step": "Bio precheck"})
        bio_check = precheck_bio(bio)
        if bio_check.get("is_parent") or bio_check.get("in_relationship") or bio_check.get("too_old") or bio_check.get("other_disqualifier"):
            reasons = []
            if bio_check.get("is_parent"):
                reasons.append("parent")
            if bio_check.get("in_relationship"):
                reasons.append("in relationship")
            if bio_check.get("too_old"):
                reasons.append("too old")
            if bio_check.get("other_disqualifier"):
                reasons.append("other disqualifier")
            reason_str = ", ".join(reasons)
            q.put({"event": "follower_result", "username": username, "status": "disqualified", "reason": f"bio signals: {reason_str}"})
            stats["disqualified"] += 1
            mark_processed(target_username, username)
            return

    # Step 3: Vision precheck
    pic_url = get_instagram_pic_url(raw_apify_data)
    if not pic_url:
        q.put({"event": "follower_result", "username": username, "status": "disqualified", "reason": "no profile picture available"})
        stats["disqualified"] += 1
        mark_processed(target_username, username)
        return

    q.put({"event": "follower_step", "username": username, "step": "Vision precheck"})
    precheck = precheck_profile_pic(pic_url)

    passed, reason = qualify_follower_vision(precheck)
    if not passed:
        q.put({"event": "follower_result", "username": username, "status": "disqualified", "reason": reason})
        stats["disqualified"] += 1
        mark_processed(target_username, username)
        return

    # Step 4: Full analysis
    q.put({"event": "follower_step", "username": username, "step": "Full analysis (engagement + vision)"})

    try:
        engagement_data = calculate_engagement(raw_apify_data, profile_data["followers"])
    except Exception:
        engagement_data = {"avg_likes": None, "avg_comments": None, "engagement_rate": None}

    vision_data = analyze_profile_pic(pic_url)

    # Re-check age from full analysis
    full_age = vision_data.get("estimated_age")
    if full_age is not None and (full_age < 18 or full_age > 36):
        q.put({"event": "follower_result", "username": username, "status": "disqualified", "reason": f"full analysis estimated age {full_age} outside 18-36 range"})
        stats["disqualified"] += 1
        mark_processed(target_username, username)
        return

    # Step 5: Upload pic + Save
    q.put({"event": "follower_step", "username": username, "step": "Uploading profile pic"})
    upload_profile_pic(username, pic_url)

    q.put({"event": "follower_step", "username": username, "step": "Saving to database"})
    merged = {**profile_data, **engagement_data, **vision_data}
    if location_data:
        merged.update(location_data)

    try:
        write_to_supabase(merged)
        q.put({"event": "follower_result", "username": username, "status": "qualified", "reason": "saved to database", "data": merged})
        stats["qualified"] += 1
        mark_processed(target_username, username)
    except Exception as e:
        q.put({"event": "follower_result", "username": username, "status": "error", "reason": f"Supabase write failed: {e}"})
        stats["errors"] += 1


# ---------------------------------------------------------------------------
# GET /api/scan/{scan_id}/stream  —  SSE stream
# ---------------------------------------------------------------------------

@app.get("/api/scan/{scan_id}/stream")
async def scan_stream(scan_id: str):
    if scan_id not in active_scans:
        raise HTTPException(status_code=404, detail="Scan not found")

    q = active_scans[scan_id]["queue"]

    async def event_generator():
        import json
        while True:
            try:
                event = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: q.get(timeout=1)
                )
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("event") == "complete":
                    break
            except queue.Empty:
                # Send keepalive
                yield ": keepalive\n\n"
                # Check if scan thread is still alive
                scan = active_scans.get(scan_id)
                if scan and not scan["thread"].is_alive() and q.empty():
                    break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# POST /api/scan/{scan_id}/stop  —  Cancel a scan
# ---------------------------------------------------------------------------

@app.post("/api/scan/{scan_id}/stop")
def stop_scan(scan_id: str):
    if scan_id not in active_scans:
        raise HTTPException(status_code=404, detail="Scan not found")
    active_scans[scan_id]["stop"].set()
    return {"status": "stopping"}


# ---------------------------------------------------------------------------
# GET /api/profiles  —  List profiles from Supabase
# ---------------------------------------------------------------------------

@app.get("/api/profiles")
def list_profiles(
    search: Optional[str] = Query(None),
    sort_by: str = Query("last_scraped"),
    sort_dir: str = Query("desc"),
    min_followers: Optional[int] = Query(None),
    max_followers: Optional[int] = Query(None),
    location_slug: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
):
    from supabase import create_client

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise HTTPException(status_code=500, detail="Supabase credentials not configured")

    supabase = create_client(url, key)

    # Build query
    query = supabase.table("profiles").select("*", count="exact")

    if search:
        query = query.or_(f"username.ilike.%{search}%,name.ilike.%{search}%")

    if min_followers is not None:
        query = query.gte("followers", min_followers)
    if max_followers is not None:
        query = query.lte("followers", max_followers)
    if location_slug:
        query = query.eq("location_slug", location_slug)

    # Sort
    ascending = sort_dir.lower() == "asc"
    query = query.order(sort_by, desc=not ascending)

    # Pagination
    offset = (page - 1) * per_page
    query = query.range(offset, offset + per_page - 1)

    result = query.execute()

    # Inject storage pic URLs for frontend display
    for profile in result.data:
        profile["profile_image_url"] = get_storage_pic_url(profile["username"])

    return {
        "data": result.data,
        "total": result.count,
        "page": page,
        "per_page": per_page,
    }


# ---------------------------------------------------------------------------
# GET /api/profiles/{username}  —  Single profile
# ---------------------------------------------------------------------------

@app.get("/api/profiles/{username}")
def get_profile(username: str):
    from supabase import create_client

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise HTTPException(status_code=500, detail="Supabase credentials not configured")

    supabase = create_client(url, key)
    result = supabase.table("profiles").select("*").eq("username", username).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail=f"Profile @{username} not found")

    profile = result.data[0]
    profile["profile_image_url"] = get_storage_pic_url(profile["username"])

    return {"data": profile}


# ===========================================================================
# ADMIN / RECOMMENDATION BACKEND ENDPOINTS
# ===========================================================================
# These endpoints exist to let you exercise the recommendation pipeline from
# the frontend admin page. They're intentionally unauthenticated for the
# prototype — wrap with auth before exposing publicly.
# ===========================================================================

class CreateCityRequest(BaseModel):
    name: str
    state: str
    country: str = "US"
    lat: Optional[float] = None
    lng: Optional[float] = None


class CreateUserRequest(BaseModel):
    email: str
    name: Optional[str] = None
    city_id: str
    lat: Optional[float] = None
    lng: Optional[float] = None


@app.post("/api/admin/cities")
def admin_create_city(req: CreateCityRequest):
    """Create a city row. Starts in 'seeding' status."""
    supabase = _supabase_client()
    payload = {
        "name": req.name,
        "state": req.state,
        "country": req.country,
        "lat": req.lat,
        "lng": req.lng,
        "status": "seeding",
    }
    try:
        result = supabase.table("cities").upsert(
            payload, on_conflict="name,state,country"
        ).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Create city failed: {e}")
    return {"city": result.data[0] if result.data else payload}


@app.get("/api/admin/cities")
def admin_list_cities():
    supabase = _supabase_client()
    res = supabase.table("cities").select("*").order("created_at").execute()
    return {"cities": res.data or []}


@app.post("/api/admin/cities/{city_id}/seed")
def admin_seed_city(city_id: str, max_anchors: int | None = None):
    """Run the Claude seeder for this city synchronously. Takes 30s–2min depending
    on how many candidates pass validation.

    Query param `max_anchors`: if set, stop after that many eligible anchors have
    been inserted (useful for cheap testing).
    """
    from workers.city_seeder import seed_city
    try:
        stats = seed_city(city_id, max_anchors=max_anchors)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Seed failed: {e}")
    return {"stats": stats}


@app.get("/api/admin/cities/{city_id}/anchors")
def admin_list_anchors(city_id: str):
    supabase = _supabase_client()
    res = (
        supabase.table("anchor_locations")
        .select("*")
        .eq("city_id", city_id)
        .order("follower_count", desc=True)
        .execute()
    )
    return {"anchors": res.data or []}


@app.post("/api/admin/users")
def admin_create_user(req: CreateUserRequest):
    """Create a test user. Computes first_eligible_drop_at via the SQL helper."""
    supabase = _supabase_client()
    # Compute first_eligible_drop_at via the Postgres function
    try:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        helper = supabase.rpc("compute_first_eligible_drop", {"p_signup_time": now_iso}).execute()
        first_eligible = helper.data
    except Exception:
        first_eligible = None

    payload = {
        "email": req.email,
        "name": req.name,
        "city_id": req.city_id,
        "lat": req.lat,
        "lng": req.lng,
        "first_eligible_drop_at": first_eligible,
    }
    try:
        result = supabase.table("users").upsert(payload, on_conflict="email").execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Create user failed: {e}")
    return {"user": result.data[0] if result.data else payload}


@app.get("/api/admin/users")
def admin_list_users():
    supabase = _supabase_client()
    res = supabase.table("users").select("*").order("created_at", desc=True).execute()
    return {"users": res.data or []}


@app.post("/api/admin/drop_prep")
def admin_run_drop_prep():
    """Run a full drop-prep pass NOW. This is the expensive endpoint — it will
    spend Apify + Grok credits proportional to how many users have gaps."""
    from workers.drop_prep import run_drop_prep
    try:
        stats = run_drop_prep()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Drop prep failed: {e}")
    return {"stats": stats}


@app.post("/api/admin/run_drop")
def admin_run_drop():
    """Execute the drop now — inserts up to 10 recommendations per eligible user."""
    from workers.drop_prep import run_drop_execution
    try:
        stats = run_drop_execution()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Drop execution failed: {e}")
    return {"stats": stats}


@app.get("/api/admin/users/{user_id}/recommendations")
def admin_user_recs(user_id: str):
    """Return this user's recommendations + their current dry-run candidate count."""
    supabase = _supabase_client()

    recs = (
        supabase.table("user_recommendations")
        .select("*")
        .eq("user_id", user_id)
        .order("drop_date", desc=True)
        .order("rank")
        .execute()
    ).data or []

    # Enrich with profile data for display
    if recs:
        usernames = list({r["profile_username"] for r in recs})
        profs = (
            supabase.table("profiles")
            .select("username,name,followers,attractiveness_rating,city_id,analysis_state")
            .in_("username", usernames)
            .execute()
        ).data or []
        prof_map = {p["username"]: p for p in profs}
        for r in recs:
            p = prof_map.get(r["profile_username"], {})
            r["profile"] = p
            r["profile_image_url"] = get_storage_pic_url(r["profile_username"])

    # Dry-run candidate count (what a drop right now WOULD give them)
    try:
        from datetime import date
        dry = supabase.rpc(
            "curate_drop_for_user",
            {"p_user_id": user_id, "p_drop_date": date.today().isoformat(), "p_dry_run": True},
        ).execute()
        available_now = len(dry.data or [])
    except Exception:
        available_now = None

    return {
        "recommendations": recs,
        "available_now": available_now,
    }


@app.get("/api/admin/state")
def admin_state():
    """One-shot snapshot of system state for the admin UI."""
    supabase = _supabase_client()

    def safe_count(table, **filters):
        try:
            q = supabase.table(table).select("*", count="exact")
            for k, v in filters.items():
                q = q.eq(k, v)
            return q.execute().count or 0
        except Exception:
            return 0

    return {
        "cities_total": safe_count("cities"),
        "cities_active": safe_count("cities", status="active"),
        "anchors_pending": safe_count("anchor_locations", status="pending"),
        "anchors_done": safe_count("anchor_locations", status="done"),
        "anchors_invalid": safe_count("anchor_locations", status="invalid"),
        "profiles_qualified": safe_count("profiles", analysis_state="qualified"),
        "profiles_disqualified": safe_count("profiles", analysis_state="disqualified"),
        "queue_queued": safe_count("analysis_queue", status="queued"),
        "queue_in_progress": safe_count("analysis_queue", status="in_progress"),
        "queue_failed": safe_count("analysis_queue", status="failed"),
        "users": safe_count("users"),
    }


@app.get("/api/admin/schema_check")
def admin_schema_check():
    """Quick check — returns which of the expected tables exist."""
    supabase = _supabase_client()
    results = {}
    for table in ["cities", "anchor_locations", "analysis_queue", "users", "user_recommendations"]:
        try:
            supabase.table(table).select("*").limit(1).execute()
            results[table] = True
        except Exception:
            results[table] = False

    # Check function existence via a harmless call
    try:
        supabase.rpc("compute_first_eligible_drop", {
            "p_signup_time": "2026-01-01T00:00:00Z"
        }).execute()
        results["compute_first_eligible_drop"] = True
    except Exception:
        results["compute_first_eligible_drop"] = False

    return {"schema": results, "ready": all(results.values())}
