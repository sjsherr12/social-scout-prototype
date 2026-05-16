"""
Social Scout Prototype
======================
Scrape a single Instagram profile and store the data in Supabase.
Or scan the followers of an account, qualifying and saving each one.

Usage:
    python main.py <instagram_url_or_username> [OPTIONS]

Options:
    --only <field1,field2,...>   Only scrape and write specific fields.
                                Skips vision analysis. Engagement fields
                                (avg_likes, avg_comments, engagement_rate)
                                are auto-included together.
    --skip-vision               Run Apify scrape but skip vision analysis.
    --skip-engagement           Run Apify scrape but skip engagement calc.
    --scan-followers            Scan followers of the target account instead
                                of scraping the account itself.
    --max-followers N           Max followers to process (default: all).
    --location-id UUID          Location cluster UUID to tag all scraped profiles.
    --location-slug SLUG        Location cluster slug to tag all scraped profiles.

Examples:
    python main.py natgeo                              # full scrape
    python main.py natgeo --only following             # only update following
    python main.py natgeo --only followers,following   # update multiple fields
    python main.py natgeo --skip-vision                # scrape + engagement, no vision
    python main.py natgeo --scan-followers             # scan all followers
    python main.py natgeo --scan-followers --max-followers 500
    python main.py natgeo --scan-followers --location-id abc-123 --location-slug austin-tx
"""

import sys
import time
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext

from dotenv import load_dotenv

from scraper import (
    extract_username,
    scrape_profile,
    scrape_profiles_batch,
    calculate_engagement,
    analyze_profile_pic,
    write_to_supabase,
    fetch_followers,
    load_processed,
    mark_processed,
    precheck_username,
    precheck_profile_pic,
    qualify_follower_basic,
    qualify_follower_vision,
    upload_profile_pic,
    get_instagram_pic_url,
    ProfileNotFoundError,
)

# Fields that come from Apify scraping
APIFY_FIELDS = {
    "username", "name", "followers", "following",
    "bio", "is_private", "post_count",
}

# Fields that require engagement calculation (always grouped together)
ENGAGEMENT_FIELDS = {"avg_likes", "avg_comments", "engagement_rate"}


SCAN_DELAY_SECONDS = 2  # delay between processing each follower


def process_single_follower(target_username: str, username: str, stats: dict,
                            location_data: dict = None,
                            profile_data: dict = None,
                            raw_apify_data: dict = None,
                            lock=None):
    """Process a single follower through the qualification pipeline.

    If profile_data + raw_apify_data are provided (from a batch scrape), the
    per-username Apify call is skipped.

    If `lock` is provided (threading.Lock), stats mutations and mark_processed
    writes are serialized so this function is safe to call from multiple threads.
    """
    sync = lock if lock is not None else nullcontext()

    def _record(status_key, do_mark=True):
        with sync:
            stats[status_key] += 1
            if do_mark:
                mark_processed(target_username, username)

    # Step 1: Scrape profile metadata via Apify (skipped if pre-scraped)
    if profile_data is None or raw_apify_data is None:
        profile_data, raw_apify_data = scrape_profile(username)

    # Step 2: Free qualification checks (follower count, account type)
    passed, reason = qualify_follower_basic(profile_data, raw_apify_data)
    if not passed:
        print(f"    SKIP @{username}: {reason}")
        _record("disqualified")
        return

    # Step 2b: Follower ratio precheck
    follower_ratio = profile_data.get("follower_ratio")
    if follower_ratio is not None and follower_ratio < 0.9:
        print(f"    SKIP @{username}: follower ratio too low ({follower_ratio})")
        _record("disqualified")
        return

    # Step 2c: Engagement rate precheck (only for non-private accounts)
    if not profile_data.get("is_private", False):
        eng = calculate_engagement(raw_apify_data, profile_data.get("followers", 0))
        eng_rate = eng.get("engagement_rate")
        if eng_rate is not None and eng_rate < 5:
            print(f"    SKIP @{username}: engagement rate too low ({eng_rate}%)")
            _record("disqualified")
            return

    # Step 2d: Username precheck (Grok text — runs only after numerical filters pass)
    if not precheck_username(username):
        print(f"    SKIP @{username}: username sounds like a business/organization")
        _record("disqualified")
        return

    # Step 3: Vision precheck (lightweight — visible person, gender, age)
    pic_url = get_instagram_pic_url(raw_apify_data)
    if not pic_url:
        print(f"    SKIP @{username}: no profile picture available")
        _record("disqualified")
        return

    precheck = precheck_profile_pic(pic_url)

    passed, reason = qualify_follower_vision(precheck)
    if not passed:
        print(f"    SKIP @{username}: {reason}")
        _record("disqualified")
        return

    # Step 4: QUALIFIED — full scrape
    print(f"    QUALIFIED @{username} — running full analysis …")

    # Engagement (reuses already-fetched raw Apify data)
    try:
        engagement_data = calculate_engagement(raw_apify_data, profile_data["followers"])
    except Exception as e:
        print(f"    ⚠ Engagement calculation failed for @{username}: {e}")
        engagement_data = {"avg_likes": None, "avg_comments": None, "engagement_rate": None}

    # Full vision analysis (ethnicity, hair, attractiveness + estimated_age)
    vision_data = analyze_profile_pic(pic_url)

    # Re-check age from full analysis (can differ from precheck estimate)
    full_age = vision_data.get("estimated_age")
    if full_age is not None and (full_age < 18 or full_age > 28):
        print(f"    SKIP @{username}: full analysis estimated age {full_age} outside 18-28 range")
        _record("disqualified")
        return

    # Upload profile picture to Supabase Storage
    upload_profile_pic(username, pic_url)

    # Merge and save
    merged = {**profile_data, **engagement_data, **vision_data}
    if location_data:
        merged.update(location_data)

    try:
        write_to_supabase(merged)
        print(f"    ✓ @{username} saved to database!")
        _record("qualified")
    except Exception as e:
        print(f"    ✗ Supabase write failed for @{username}: {e}")
        with sync:
            stats["errors"] += 1


def scan_followers(username: str, max_followers: int, location_data: dict = None):
    """Orchestrate the follower scanning pipeline."""

    print(f"\n{'='*50}")
    print(f"  Social Scout — scanning followers of @{username}")
    if max_followers:
        print(f"  Max followers: {max_followers}")
    else:
        print(f"  Max followers: all")
    if location_data:
        print(f"  Location: {location_data}")
    print(f"{'='*50}\n")

    # Load already-processed usernames for resume support
    already_processed = load_processed(username)
    if already_processed:
        print(f"  Resuming — {len(already_processed)} followers already processed\n")

    # Determine how many followers to fetch from Apify.
    # If max_followers is set, only fetch enough to cover already-processed + desired count.
    # This avoids fetching thousands when you only want to process a few.
    if max_followers:
        fetch_count = len(already_processed) + max_followers
    else:
        fetch_count = 100000  # effectively all

    print("[1/2] Fetching followers list …")
    try:
        follower_usernames = fetch_followers(username, fetch_count=fetch_count)
    except ProfileNotFoundError as e:
        print(f"  ✗ {e}")
        sys.exit(1)
    except Exception as e:
        print(f"  ✗ Failed to fetch followers: {type(e).__name__}: {e}")
        sys.exit(1)

    total_fetched = len(follower_usernames)

    # Filter out already-processed followers
    remaining = [u for u in follower_usernames if u not in already_processed]
    skipped = total_fetched - len(remaining)

    print(f"  ✓ Fetched {total_fetched} followers from Apify")
    if skipped > 0:
        print(f"  ✓ Skipping {skipped} already processed — {len(remaining)} remaining")

    # Cap how many we process this run
    if max_followers and len(remaining) > max_followers:
        remaining = remaining[:max_followers]
        print(f"  ✓ Processing {max_followers} this run")
    print()

    if not remaining:
        print("  Nothing to do — all followers already processed.")
        return

    # Process followers in batches — one Apify run per batch, then run each
    # follower's precheck pipeline in parallel via a thread pool.
    print("[2/2] Processing followers …\n")
    stats = {"qualified": 0, "disqualified": 0, "errors": 0}
    total = len(remaining)
    BATCH_SIZE = 50
    MAX_WORKERS = 10

    stats_lock = threading.Lock()

    def _worker(follower: str, profile_data: dict, raw_apify_data: dict):
        try:
            process_single_follower(
                username, follower, stats, location_data,
                profile_data=profile_data, raw_apify_data=raw_apify_data,
                lock=stats_lock,
            )
        except ProfileNotFoundError as e:
            print(f"    SKIP @{follower}: {e}")
            with stats_lock:
                stats["errors"] += 1
                mark_processed(username, follower)
        except Exception as e:
            print(f"    ERROR @{follower}: {type(e).__name__}: {e}")
            with stats_lock:
                stats["errors"] += 1

    processed_count = 0
    for batch_start in range(0, total, BATCH_SIZE):
        batch = remaining[batch_start:batch_start + BATCH_SIZE]
        print(f"  Batch {batch_start // BATCH_SIZE + 1}: scraping {len(batch)} profiles …")

        try:
            scraped = scrape_profiles_batch(batch)
        except Exception as e:
            print(f"    ⚠ Batch scrape failed: {type(e).__name__}: {e} — skipping batch")
            with stats_lock:
                for _ in batch:
                    stats["errors"] += 1
            continue

        to_process = []
        for follower in batch:
            processed_count += 1
            entry = scraped.get(follower.lower())
            if entry is None:
                print(f"  [{processed_count}/{total}] @{follower} — SKIP: profile not returned by Apify")
                with stats_lock:
                    stats["errors"] += 1
                    mark_processed(username, follower)
                continue
            to_process.append((follower, entry))

        print(f"  Running {len(to_process)} prechecks in parallel (max {MAX_WORKERS} concurrent) …")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [
                executor.submit(_worker, follower, entry[0], entry[1])
                for follower, entry in to_process
            ]
            for fut in futures:
                fut.result()

    # Summary
    processed = stats["qualified"] + stats["disqualified"] + stats["errors"]
    print(f"\n{'='*50}")
    print(f"  Scan complete for @{username}")
    print(f"  Processed this run: {processed} | Qualified: {stats['qualified']} | "
          f"Disqualified: {stats['disqualified']} | Errors: {stats['errors']}")
    print(f"  Total processed (all runs): {len(already_processed) + processed}")
    print(f"{'='*50}\n")


def main():
    parser = argparse.ArgumentParser(description="Social Scout — Instagram profile scraper")
    parser.add_argument("profile", help="Instagram URL or username")
    parser.add_argument("--only", type=str, default=None,
                        help="Comma-separated list of fields to scrape (e.g. --only following,followers)")
    parser.add_argument("--skip-vision", action="store_true",
                        help="Skip vision analysis")
    parser.add_argument("--skip-engagement", action="store_true",
                        help="Skip engagement calculation")
    parser.add_argument("--scan-followers", action="store_true",
                        help="Scan followers of the target account instead of scraping it")
    parser.add_argument("--max-followers", type=int, default=None,
                        help="Max followers to fetch in scan mode (default: all)")
    parser.add_argument("--location-id", type=str, default=None,
                        help="Location cluster UUID to tag all scraped profiles")
    parser.add_argument("--location-slug", type=str, default=None,
                        help="Location cluster slug to tag all scraped profiles")
    args = parser.parse_args()

    load_dotenv()

    username = extract_username(args.profile)

    # Build location data if provided
    location_data = {}
    if args.location_id:
        location_data["location_cluster_id"] = args.location_id
    if args.location_slug:
        location_data["location_slug"] = args.location_slug

    # Branch: follower scanning mode
    if args.scan_followers:
        scan_followers(username, args.max_followers, location_data)
        return

    # Determine what to run
    only_fields = None
    if args.only:
        only_fields = set(f.strip() for f in args.only.split(","))
        # If any engagement field is requested, include all of them
        if only_fields & ENGAGEMENT_FIELDS:
            only_fields |= ENGAGEMENT_FIELDS

    run_engagement = not args.skip_engagement
    run_vision = not args.skip_vision

    # If --only is set, vision is always skipped and engagement only runs if requested
    if only_fields:
        run_vision = False
        run_engagement = bool(only_fields & ENGAGEMENT_FIELDS)

    print(f"\n{'='*50}")
    print(f"  Social Scout — scraping @{username}")
    if only_fields:
        print(f"  Mode: only [{', '.join(sorted(only_fields))}]")
    print(f"{'='*50}\n")

    # Step 1: Scrape profile metadata via Apify (always needed)
    print("[1/4] Fetching profile metadata via Apify …")
    try:
        profile_data, raw_apify_data = scrape_profile(username)
    except ProfileNotFoundError as e:
        print(f"  ✗ {e}")
        sys.exit(1)
    except Exception as e:
        print(f"  ✗ Unexpected error: {type(e).__name__}: {e}")
        sys.exit(1)

    print(f"  ✓ @{profile_data['username']} — {profile_data['followers']} followers")

    # If --only, filter profile_data to only requested Apify fields
    if only_fields:
        apify_requested = only_fields & APIFY_FIELDS
        # Always include username for the upsert on_conflict
        apify_requested.add("username")
        profile_data = {k: v for k, v in profile_data.items() if k in apify_requested}

    # Step 2: Calculate engagement metrics
    engagement_data = {}
    if run_engagement:
        print("[2/4] Calculating engagement metrics …")
        try:
            engagement_data = calculate_engagement(raw_apify_data, raw_apify_data.get("followersCount", 0))
        except Exception as e:
            print(f"  ⚠ Engagement calculation failed: {e}")
            engagement_data = {"avg_likes": None, "avg_comments": None, "engagement_rate": None}

        if engagement_data.get("avg_likes") is not None:
            print(f"  ✓ Avg likes: {engagement_data['avg_likes']}, "
                  f"Avg comments: {engagement_data['avg_comments']}, "
                  f"Engagement: {engagement_data['engagement_rate']}%")
        else:
            print("  ⚠ Skipped (private profile or no posts)")
    else:
        print("[2/4] Skipping engagement metrics.")

    # Step 3: Analyze profile picture with Grok vision
    vision_data = {}
    pic_url = get_instagram_pic_url(raw_apify_data)
    if run_vision:
        print("[3/4] Analyzing profile picture …")
        if pic_url:
            vision_data = analyze_profile_pic(pic_url)
            if vision_data.get("estimated_age") is not None:
                print(f"  ✓ Age: ~{vision_data['estimated_age']}, "
                      f"Ethnicity: {vision_data['ethnicity']}, "
                      f"Hair: {vision_data['hair_color']}, "
                      f"Attractiveness: {vision_data['attractiveness_rating']}")
            else:
                print("  ⚠ Vision analysis unavailable")
        else:
            print("  ⚠ No profile picture URL available")
    else:
        print("[3/4] Skipping vision analysis.")

    # Upload profile picture to Supabase Storage
    if pic_url:
        upload_profile_pic(username, pic_url)

    # Step 4: Write to Supabase
    print("[4/4] Writing to Supabase …")
    merged_data = {**profile_data, **engagement_data, **vision_data}

    try:
        write_to_supabase(merged_data)
        print("  ✓ Data saved successfully!")
    except Exception as e:
        print(f"  ✗ Supabase write failed: {e}")
        print("\n  Fallback — scraped data:")
        for k, v in merged_data.items():
            print(f"    {k}: {v}")
        sys.exit(1)

    # Summary
    print(f"\n{'='*50}")
    print(f"  Done! Profile @{username} scraped and saved.")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
