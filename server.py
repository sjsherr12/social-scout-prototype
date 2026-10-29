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
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
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
    scrape_profiles_batch,
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


class KeywordScanRequest(BaseModel):
    username: str
    keyword: str
    max_followers: Optional[int] = None


# ---------------------------------------------------------------------------
# In-memory scan state
# ---------------------------------------------------------------------------

# Maps scan_id -> { "queue": Queue, "thread": Thread, "stop": Event }
active_scans: dict[str, dict] = {}

# Keyword bio-scans are tracked entirely separately from the qualification
# pipeline above. Maps scan_id -> { "queue", "thread", "stop", "matches",
# "keyword", "username" }
active_keyword_scans: dict[str, dict] = {}


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

        stats = {"qualified": 0, "disqualified": 0, "errors": 0}
        q.put({"event": "scan_started", "total": total})

        BATCH_SIZE = 50
        MAX_WORKERS = 10

        stats_lock = threading.Lock()
        progress_counter = {"n": 0}

        def _worker(follower: str, pdata: dict, raw: dict):
            if stop_event.is_set():
                return
            try:
                _process_single_follower_for_scan(
                    username, follower, stats, location_data, q,
                    profile_data=pdata, raw_apify_data=raw,
                    lock=stats_lock,
                )
            except ProfileNotFoundError as e:
                q.put({"event": "follower_result", "username": follower, "status": "error", "reason": str(e)})
                with stats_lock:
                    stats["errors"] += 1
                    mark_processed(username, follower)
            except Exception as e:
                q.put({"event": "follower_result", "username": follower, "status": "error", "reason": f"{type(e).__name__}: {e}"})
                with stats_lock:
                    stats["errors"] += 1
            finally:
                with stats_lock:
                    progress_counter["n"] += 1
                    current = progress_counter["n"]
                    q.put({"event": "progress", "current": current, "total": total, "stats": dict(stats)})

        stopped = False
        for batch_start in range(0, total, BATCH_SIZE):
            if stop_event.is_set():
                q.put({"event": "info", "message": "Scan stopped by user"})
                stopped = True
                break

            batch = remaining[batch_start:batch_start + BATCH_SIZE]
            q.put({"event": "status", "message": f"Batch-scraping {len(batch)} profiles..."})

            try:
                scraped = scrape_profiles_batch(batch)
            except Exception as e:
                q.put({"event": "error", "message": f"Batch scrape failed: {e}"})
                for follower in batch:
                    with stats_lock:
                        stats["errors"] += 1
                        progress_counter["n"] += 1
                        current = progress_counter["n"]
                        q.put({"event": "follower_result", "username": follower, "status": "error", "reason": "batch scrape failed"})
                        q.put({"event": "progress", "current": current, "total": total, "stats": dict(stats)})
                continue

            to_process = []
            for follower in batch:
                entry = scraped.get(follower.lower())
                if entry is None:
                    with stats_lock:
                        stats["errors"] += 1
                        mark_processed(username, follower)
                        progress_counter["n"] += 1
                        current = progress_counter["n"]
                        q.put({"event": "follower_result", "username": follower, "status": "error", "reason": "profile not returned by Apify (may not exist or be private)"})
                        q.put({"event": "progress", "current": current, "total": total, "stats": dict(stats)})
                    continue
                to_process.append((follower, entry))

            q.put({"event": "status", "message": f"Running {len(to_process)} prechecks in parallel (max {MAX_WORKERS} concurrent)..."})

            for follower, _ in to_process:
                q.put({"event": "follower_start", "username": follower, "total": total})

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = [
                    executor.submit(_worker, follower, entry[0], entry[1])
                    for follower, entry in to_process
                ]
                for fut in futures:
                    fut.result()

            if stop_event.is_set():
                q.put({"event": "info", "message": "Scan stopped by user"})
                stopped = True
                break

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
    profile_data: dict = None,
    raw_apify_data: dict = None,
    lock=None,
):
    """Process a single follower, pushing step-by-step events to the queue.

    If profile_data + raw_apify_data are provided (from a batch scrape), the
    per-username Apify call is skipped.

    If `lock` is provided, stats mutations and mark_processed writes are
    serialized so this function is safe to call from multiple threads.
    """
    sync = lock if lock is not None else nullcontext()

    def _record(status_key, do_mark=True):
        with sync:
            stats[status_key] += 1
            if do_mark:
                mark_processed(target_username, username)

    # Step 1: Scrape profile (skipped if pre-scraped)
    if profile_data is None or raw_apify_data is None:
        q.put({"event": "follower_step", "username": username, "step": "Scraping profile"})
        profile_data, raw_apify_data = scrape_profile(username)

    # Step 2: Basic qualification
    q.put({"event": "follower_step", "username": username, "step": "Basic qualification"})
    passed, reason = qualify_follower_basic(profile_data, raw_apify_data)
    if not passed:
        q.put({"event": "follower_result", "username": username, "status": "disqualified", "reason": reason})
        _record("disqualified")
        return

    # Step 2b: Follower ratio precheck
    follower_ratio = profile_data.get("follower_ratio")
    if follower_ratio is not None and follower_ratio < 0.9:
        q.put({"event": "follower_result", "username": username, "status": "disqualified", "reason": f"follower ratio too low ({follower_ratio})"})
        _record("disqualified")
        return

    # Step 2c: Engagement rate precheck (only for non-private accounts)
    if not profile_data.get("is_private", False):
        eng = calculate_engagement(raw_apify_data, profile_data.get("followers", 0))
        eng_rate = eng.get("engagement_rate")
        if eng_rate is not None and eng_rate < 5:
            q.put({"event": "follower_result", "username": username, "status": "disqualified", "reason": f"engagement rate too low ({eng_rate}%)"})
            _record("disqualified")
            return

    # Step 2d: Username precheck (Grok text — runs only after numerical filters pass)
    q.put({"event": "follower_step", "username": username, "step": "Username precheck"})
    if not precheck_username(username):
        q.put({"event": "follower_result", "username": username, "status": "disqualified", "reason": "username sounds like a business/organization"})
        _record("disqualified")
        return

    # Step 2c: Bio precheck
    bio = profile_data.get("bio") or ""
    if bio.strip():
        q.put({"event": "follower_step", "username": username, "step": "Bio precheck"})
        bio_check = precheck_bio(bio)
        if bio_check.get("is_parent") or bio_check.get("in_relationship") or bio_check.get("too_old") or bio_check.get("is_political") or bio_check.get("other_disqualifier"):
            reasons = []
            if bio_check.get("is_parent"):
                reasons.append("parent")
            if bio_check.get("in_relationship"):
                reasons.append("in relationship")
            if bio_check.get("too_old"):
                reasons.append("too old")
            if bio_check.get("is_political"):
                reasons.append("political content")
            if bio_check.get("other_disqualifier"):
                reasons.append("other disqualifier")
            reason_str = ", ".join(reasons)
            q.put({"event": "follower_result", "username": username, "status": "disqualified", "reason": f"bio signals: {reason_str}"})
            _record("disqualified")
            return

    # Step 3: Vision precheck
    pic_url = get_instagram_pic_url(raw_apify_data)
    if not pic_url:
        q.put({"event": "follower_result", "username": username, "status": "disqualified", "reason": "no profile picture available"})
        _record("disqualified")
        return

    q.put({"event": "follower_step", "username": username, "step": "Vision precheck"})
    precheck = precheck_profile_pic(pic_url)

    passed, reason = qualify_follower_vision(precheck)
    if not passed:
        q.put({"event": "follower_result", "username": username, "status": "disqualified", "reason": reason})
        _record("disqualified")
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
    if full_age is not None and (full_age < 18 or full_age > 28):
        q.put({"event": "follower_result", "username": username, "status": "disqualified", "reason": f"full analysis estimated age {full_age} outside 18-28 range"})
        _record("disqualified")
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
        _record("qualified")
    except Exception as e:
        q.put({"event": "follower_result", "username": username, "status": "error", "reason": f"Supabase write failed: {e}"})
        with sync:
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


# ===========================================================================
# Keyword Bio-Scan  —  completely separate from the qualification pipeline.
#
# Fetches a target account's followers, scrapes each one, and collects any
# whose bio contains a given keyword. No qualification, no vision analysis,
# no Supabase writes — results are streamed live and held in memory only.
# ===========================================================================

# ---------------------------------------------------------------------------
# POST /api/keyword-scan  —  Start a keyword bio-scan
# ---------------------------------------------------------------------------

@app.post("/api/keyword-scan")
def start_keyword_scan(req: KeywordScanRequest):
    username = extract_username(req.username)
    keyword = (req.keyword or "").strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="Keyword must not be empty")

    scan_id = str(uuid.uuid4())
    q = queue.Queue()
    stop_event = threading.Event()

    thread = threading.Thread(
        target=_run_keyword_scan,
        args=(scan_id, username, keyword, req.max_followers, q, stop_event),
        daemon=True,
    )

    active_keyword_scans[scan_id] = {
        "queue": q,
        "thread": thread,
        "stop": stop_event,
        "matches": [],
        "keyword": keyword,
        "username": username,
    }
    thread.start()

    return {"scan_id": scan_id, "username": username, "keyword": keyword}


def _run_keyword_scan(
    scan_id: str,
    username: str,
    keyword: str,
    max_followers: Optional[int],
    q: queue.Queue,
    stop_event: threading.Event,
):
    """Fetch the target's followers, scrape each, and collect bios containing the keyword."""
    state = active_keyword_scans[scan_id]
    matches = state["matches"]
    keyword_lower = keyword.lower()

    try:
        # Fetch the full followers list
        q.put({"event": "status", "message": f"Fetching followers of @{username}..."})
        fetch_count = max_followers if max_followers else 100000

        try:
            follower_usernames = fetch_followers(username, fetch_count=max(fetch_count, 50))
        except Exception as e:
            q.put({"event": "error", "message": f"Failed to fetch followers: {e}"})
            q.put({"event": "complete", "stats": {"matched": 0, "scanned": 0, "errors": 1}})
            return

        if max_followers and len(follower_usernames) > max_followers:
            follower_usernames = follower_usernames[:max_followers]

        total = len(follower_usernames)
        q.put({"event": "info", "message": f"Fetched {total} followers — scanning bios for \"{keyword}\""})

        if total == 0:
            q.put({"event": "complete", "stats": {"matched": 0, "scanned": 0, "errors": 0}})
            return

        q.put({"event": "scan_started", "total": total})
        stats = {"matched": 0, "scanned": 0, "errors": 0}

        BATCH_SIZE = 50

        for batch_start in range(0, total, BATCH_SIZE):
            if stop_event.is_set():
                q.put({"event": "info", "message": "Scan stopped by user"})
                break

            batch = follower_usernames[batch_start:batch_start + BATCH_SIZE]
            q.put({"event": "status", "message": f"Scraping {len(batch)} profiles..."})

            try:
                scraped = scrape_profiles_batch(batch)
            except Exception as e:
                q.put({"event": "error", "message": f"Batch scrape failed: {e}"})
                stats["errors"] += len(batch)
                stats["scanned"] += len(batch)
                q.put({"event": "progress", "current": stats["scanned"], "total": total, "stats": dict(stats)})
                continue

            for follower in batch:
                stats["scanned"] += 1
                entry = scraped.get(follower.lower())
                if entry is None:
                    stats["errors"] += 1
                    continue

                profile_data, raw = entry
                bio = profile_data.get("bio") or ""
                if keyword_lower in bio.lower():
                    resolved = profile_data.get("username") or follower
                    match = {
                        "username": resolved,
                        "name": profile_data.get("name"),
                        "bio": bio,
                        "followers": profile_data.get("followers"),
                        "following": profile_data.get("following"),
                        "is_private": profile_data.get("is_private"),
                        "profile_url": f"https://www.instagram.com/{resolved}",
                        "profile_image_url": get_instagram_pic_url(raw),
                    }
                    matches.append(match)
                    stats["matched"] += 1
                    q.put({"event": "match", **match})

            q.put({"event": "progress", "current": stats["scanned"], "total": total, "stats": dict(stats)})

        q.put({"event": "complete", "stats": dict(stats)})

    except Exception as e:
        q.put({"event": "error", "message": f"Scan failed: {e}"})
        q.put({"event": "complete", "stats": {"matched": len(matches), "scanned": 0, "errors": 1}})
    finally:
        # Keep results around for a while so the page can re-fetch them, then clean up.
        def cleanup():
            time.sleep(600)
            active_keyword_scans.pop(scan_id, None)
        threading.Thread(target=cleanup, daemon=True).start()


# ---------------------------------------------------------------------------
# GET /api/keyword-scan/{scan_id}/stream  —  SSE stream
# ---------------------------------------------------------------------------

@app.get("/api/keyword-scan/{scan_id}/stream")
async def keyword_scan_stream(scan_id: str):
    if scan_id not in active_keyword_scans:
        raise HTTPException(status_code=404, detail="Scan not found")

    q = active_keyword_scans[scan_id]["queue"]

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
                yield ": keepalive\n\n"
                scan = active_keyword_scans.get(scan_id)
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
# POST /api/keyword-scan/{scan_id}/stop  —  Cancel a keyword scan
# ---------------------------------------------------------------------------

@app.post("/api/keyword-scan/{scan_id}/stop")
def stop_keyword_scan(scan_id: str):
    if scan_id not in active_keyword_scans:
        raise HTTPException(status_code=404, detail="Scan not found")
    active_keyword_scans[scan_id]["stop"].set()
    return {"status": "stopping"}


# ---------------------------------------------------------------------------
# GET /api/keyword-scan/{scan_id}/results  —  Fetch collected matches
# ---------------------------------------------------------------------------

@app.get("/api/keyword-scan/{scan_id}/results")
def keyword_scan_results(scan_id: str):
    scan = active_keyword_scans.get(scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found or expired")
    return {
        "keyword": scan["keyword"],
        "username": scan["username"],
        "matches": list(scan["matches"]),
    }


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
