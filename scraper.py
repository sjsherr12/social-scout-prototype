import os
import re
import json
import base64
from datetime import datetime, timezone

import requests as req
from openai import OpenAI
from supabase import create_client
from apify_client import ApifyClient


# ---------------------------------------------------------------------------
# 1. Username extraction
# ---------------------------------------------------------------------------

def extract_username(input_str: str) -> str:
    """Extract username from an Instagram URL or bare username."""
    input_str = input_str.strip().rstrip("/")
    match = re.search(r"(?:instagram\.com/)([A-Za-z0-9_.]+)", input_str)
    if match:
        return match.group(1)
    return input_str.split("/")[-1]


# ---------------------------------------------------------------------------
# 2. Profile scraping via Apify
# ---------------------------------------------------------------------------

ACTOR_ID = "dSCLg0C3YEZ83HzYX"  # apify/instagram-profile-scraper


def scrape_profile(username: str) -> dict:
    """
    Fetch profile metadata using Apify's Instagram Profile Scraper.
    Returns a dict with all profile fields.
    """
    token = os.getenv("APIFY_API_TOKEN")
    if not token:
        raise RuntimeError("APIFY_API_TOKEN must be set in .env")

    client = ApifyClient(token)

    run_input = {
        "usernames": [username],
    }

    print(f"  Running Apify scraper for @{username} …")
    run = client.actor(ACTOR_ID).call(run_input=run_input, timeout_secs=120)

    # Fetch results from the run's dataset
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())

    if not items:
        raise ProfileNotFoundError(
            f"Apify returned no data for @{username}. "
            f"The profile may not exist or may be private."
        )

    raw = items[0]  # we only scraped one profile

    # Map Apify fields to our schema
    followers = raw.get("followersCount", 0)
    post_count = raw.get("postsCount", 0)

    data = {
        "username": raw.get("username", username),
        "profile_url": f"https://www.instagram.com/{raw.get('username', username)}",
        "name": raw.get("fullName"),
        "followers": followers,
        "following": raw.get("followsCount", 0),
        "bio": raw.get("biography"),
        "profile_image_url": raw.get("profilePicUrlHD") or raw.get("profilePicUrl"),
        "is_private": raw.get("private", False),
        "post_count": post_count,
    }

    return data, raw


# ---------------------------------------------------------------------------
# 3. Engagement metrics (calculated from Apify's recent posts data)
# ---------------------------------------------------------------------------

MAX_POSTS = 12  # use up to 12 recent posts for engagement calculation


def calculate_engagement(raw_apify_data: dict, followers: int) -> dict:
    """Compute avg likes, avg comments, and engagement rate from recent posts."""
    if raw_apify_data.get("private", False):
        return {"avg_likes": None, "avg_comments": None, "engagement_rate": None}

    # Apify returns recent posts in "latestPosts"
    posts = raw_apify_data.get("latestPosts", [])[:MAX_POSTS]

    if not posts:
        return {"avg_likes": 0, "avg_comments": 0, "engagement_rate": 0.0}

    total_likes = 0
    total_comments = 0

    for post in posts:
        total_likes += post.get("likesCount", 0)
        total_comments += post.get("commentsCount", 0)

    count = len(posts)
    avg_likes = total_likes / count
    avg_comments = total_comments / count
    safe_followers = followers or 1  # avoid division by zero

    engagement_rate = round((avg_likes + avg_comments) / safe_followers * 100, 4)

    return {
        "avg_likes": int(round(avg_likes)),
        "avg_comments": int(round(avg_comments)),
        "engagement_rate": engagement_rate,
    }


# ---------------------------------------------------------------------------
# 4. Vision analysis via Grok (xAI)
# ---------------------------------------------------------------------------

VISION_PROMPT = (
    "Analyze this Instagram profile picture. Return a JSON object with exactly "
    "these fields:\n"
    '  "gender": (string — "male", "female", or "unknown"),\n'
    '  "age_min": (int — estimated minimum age),\n'
    '  "age_max": (int — estimated maximum age),\n'
    '  "ethnicity": (string — "white", "black", "asian"). Note that "white" includes Hispanics, Europeans, etc.,\n'
    '  "hair_color": (string — e.g. "black", "blonde", "brown", "red", "gray", "unknown"),\n'
    '  "attractiveness_rating": (float 0.0 to 1.0 — overall facial attractiveness, '
    '0.0 being lowest and 1.0 being highest. Use the FULL range of the scale. '
    'An average-looking girl should score around 0.5. '
    'Only genuinely attractive women should score above 0.8. '
    'Reserve 0.9+ for exceptionally attractive individuals. '
    'Be critical and realistic — rate from the perspective of a young man.\n'
    "\n"
    "If the image does not clearly show a person (e.g. a logo, pet, or group photo), "
    'set all fields to "unknown" (use 0 for age ints, and null for attractiveness_rating).\n'
    "Return ONLY valid JSON, no explanation."
)


def analyze_profile_pic(profile_pic_url: str) -> dict:
    """Download the profile picture and analyze it with Grok vision."""
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        print("  ⚠ No XAI_API_KEY found — skipping vision analysis.")
        return _empty_vision_result()

    # Download image
    try:
        resp = req.get(profile_pic_url, timeout=15)
        resp.raise_for_status()
        image_bytes = resp.content
    except req.RequestException as e:
        print(f"  ⚠ Failed to download profile picture: {e}")
        return _empty_vision_result()

    # Call Grok vision
    try:
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        client = OpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1",
        )

        response = client.chat.completions.create(
            model="grok-4.20-0309-non-reasoning",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VISION_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64_image}",
                                "detail": "low",
                            },
                        },
                    ],
                }
            ],
            max_tokens=200,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        result = json.loads(raw)

        return {
            "age_min": result.get("age_min"),
            "age_max": result.get("age_max"),
            "ethnicity": result.get("ethnicity"),
            "hair_color": result.get("hair_color"),
            "attractiveness_rating": result.get("attractiveness_rating"),
        }

    except Exception as e:
        print(f"  ⚠ Vision analysis failed: {e}")
        return _empty_vision_result()


def _empty_vision_result() -> dict:
    return {
        "age_min": None,
        "age_max": None,
        "ethnicity": None,
        "hair_color": None,
        "attractiveness_rating": None,
    }


# ---------------------------------------------------------------------------
# 5. Supabase write
# ---------------------------------------------------------------------------

def write_to_supabase(data: dict):
    """Upsert the profile data into the Supabase profiles table."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")

    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in .env")

    supabase = create_client(url, key)

    # Add timestamp
    data["last_scraped"] = datetime.now(timezone.utc).isoformat()

    # Debug: print what we're sending
    print(f"  Upserting data for @{data.get('username')} …")

    result = supabase.table("profiles").upsert(data, on_conflict="username").execute()

    # Verify the write succeeded
    if result.data:
        print(f"  ✓ Supabase returned {len(result.data)} row(s)")
    else:
        print(f"  ⚠ Supabase returned no data. Full response: {result}")

    return result


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class ProfileNotFoundError(Exception):
    pass
