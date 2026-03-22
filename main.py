"""
Social Scout Prototype
======================
Scrape a single Instagram profile and store the data in Supabase.

Usage:
    python main.py <instagram_url_or_username> [OPTIONS]

Options:
    --only <field1,field2,...>   Only scrape and write specific fields.
                                Skips vision analysis. Engagement fields
                                (avg_likes, avg_comments, engagement_rate)
                                are auto-included together.
    --skip-vision               Run Apify scrape but skip vision analysis.
    --skip-engagement           Run Apify scrape but skip engagement calc.

Examples:
    python main.py natgeo                              # full scrape
    python main.py natgeo --only following             # only update following
    python main.py natgeo --only followers,following   # update multiple fields
    python main.py natgeo --skip-vision                # scrape + engagement, no vision
"""

import sys
import argparse

from dotenv import load_dotenv

from scraper import (
    extract_username,
    scrape_profile,
    calculate_engagement,
    analyze_profile_pic,
    write_to_supabase,
    ProfileNotFoundError,
)

# Fields that come from Apify scraping
APIFY_FIELDS = {
    "username", "profile_url", "name", "followers", "following",
    "bio", "profile_image_url", "is_private", "post_count",
}

# Fields that require engagement calculation (always grouped together)
ENGAGEMENT_FIELDS = {"avg_likes", "avg_comments", "engagement_rate"}


def main():
    parser = argparse.ArgumentParser(description="Social Scout — Instagram profile scraper")
    parser.add_argument("profile", help="Instagram URL or username")
    parser.add_argument("--only", type=str, default=None,
                        help="Comma-separated list of fields to scrape (e.g. --only following,followers)")
    parser.add_argument("--skip-vision", action="store_true",
                        help="Skip vision analysis")
    parser.add_argument("--skip-engagement", action="store_true",
                        help="Skip engagement calculation")
    args = parser.parse_args()

    load_dotenv()

    username = extract_username(args.profile)

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
    if run_vision:
        print("[3/4] Analyzing profile picture …")
        pic_url = profile_data.get("profile_image_url") or raw_apify_data.get("profilePicUrlHD") or raw_apify_data.get("profilePicUrl")
        if pic_url:
            vision_data = analyze_profile_pic(pic_url)
            if vision_data.get("age_min") is not None:
                print(f"  ✓ Age: {vision_data['age_min']}-{vision_data['age_max']}, "
                      f"Ethnicity: {vision_data['ethnicity']}, "
                      f"Hair: {vision_data['hair_color']}, "
                      f"Attractiveness: {vision_data['attractiveness_rating']}")
            else:
                print("  ⚠ Vision analysis unavailable")
        else:
            print("  ⚠ No profile picture URL available")
    else:
        print("[3/4] Skipping vision analysis.")

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
