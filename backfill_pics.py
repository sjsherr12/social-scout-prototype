"""
Backfill profile pictures for existing Supabase profiles.
==========================================================
Fetches each profile via Apify to get the current Instagram pic URL,
downloads the image, and uploads it to the Supabase Storage bucket.

Usage:  python backfill_pics.py [--dry-run] [--limit N]
"""

import os
import sys
import time
import argparse

# Fix Windows console encoding for emoji/unicode characters
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import requests as req
from dotenv import load_dotenv
from supabase import create_client

from scraper import (
    scrape_profile,
    upload_profile_pic,
    get_instagram_pic_url,
    get_storage_pic_url,
    ProfileNotFoundError,
)

load_dotenv()

DELAY_SECONDS = 2  # delay between Apify calls to avoid rate limits


def get_all_usernames() -> list[str]:
    """Fetch all usernames from the Supabase profiles table."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_KEY must be set in .env")
        sys.exit(1)

    supabase = create_client(url, key)

    # Fetch all usernames (paginate in case there are many)
    all_usernames = []
    page_size = 1000
    offset = 0

    while True:
        result = (
            supabase.table("profiles")
            .select("username")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        if not result.data:
            break
        all_usernames.extend(row["username"] for row in result.data)
        if len(result.data) < page_size:
            break
        offset += page_size

    return all_usernames


def verify_pic(username: str) -> bool:
    """Check if the storage URL returns a valid image."""
    storage_url = get_storage_pic_url(username)
    try:
        resp = req.head(storage_url, timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Backfill profile pictures to Supabase Storage")
    parser.add_argument("--dry-run", action="store_true", help="Only list profiles, don't upload")
    parser.add_argument("--limit", type=int, default=None, help="Max profiles to process")
    args = parser.parse_args()

    print("\n" + "=" * 50)
    print("  Profile Picture Backfill")
    print("=" * 50 + "\n")

    # Step 1: Get all usernames
    print("[1/3] Fetching usernames from Supabase...")
    usernames = get_all_usernames()
    print(f"  Found {len(usernames)} profiles\n")

    if not usernames:
        print("  Nothing to do.")
        return

    if args.limit:
        usernames = usernames[: args.limit]
        print(f"  Limited to {args.limit} profiles\n")

    if args.dry_run:
        print("  DRY RUN — would process these profiles:")
        for u in usernames:
            print(f"    @{u}")
        return

    # Step 2: Process each profile
    print(f"[2/3] Processing {len(usernames)} profiles...\n")
    stats = {"success": 0, "failed": 0, "skipped": 0}

    for i, username in enumerate(usernames, 1):
        print(f"  [{i}/{len(usernames)}] @{username}")

        # Check if pic already exists in storage
        if verify_pic(username):
            print(f"    Already has pic in storage — skipping")
            stats["skipped"] += 1
            continue

        try:
            # Scrape profile to get current Instagram pic URL
            _profile_data, raw_apify_data = scrape_profile(username)
            pic_url = get_instagram_pic_url(raw_apify_data)

            if not pic_url:
                print(f"    No profile picture URL found — skipping")
                stats["failed"] += 1
                continue

            # Upload to storage
            upload_profile_pic(username, pic_url)
            stats["success"] += 1

        except ProfileNotFoundError:
            print(f"    Profile not found on Instagram — skipping")
            stats["failed"] += 1
        except Exception as e:
            print(f"    ERROR: {type(e).__name__}: {e}")
            stats["failed"] += 1

        # Rate limit
        if i < len(usernames):
            time.sleep(DELAY_SECONDS)

    # Step 3: Verify
    print(f"\n[3/3] Verifying uploads...\n")
    verified = 0
    failed_verify = []

    for username in usernames:
        if verify_pic(username):
            verified += 1
        else:
            failed_verify.append(username)

    # Summary
    print(f"\n{'=' * 50}")
    print(f"  Backfill complete!")
    print(f"  Uploaded: {stats['success']} | Skipped (already existed): {stats['skipped']} | Failed: {stats['failed']}")
    print(f"  Verified: {verified}/{len(usernames)} pics accessible")
    if failed_verify:
        print(f"  Missing pics: {', '.join(f'@{u}' for u in failed_verify)}")
    print(f"{'=' * 50}\n")


if __name__ == "__main__":
    main()
