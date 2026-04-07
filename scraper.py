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
        "name": raw.get("fullName"),
        "followers": followers,
        "following": raw.get("followsCount", 0),
        "bio": raw.get("biography"),
        "is_private": raw.get("private", False),
        "post_count": post_count,
    }

    return data, raw


# ---------------------------------------------------------------------------
# 2b. Followers list fetching via Apify
# ---------------------------------------------------------------------------

FOLLOWERS_ACTOR_ID = "datadoping/instagram-followers-scraper"


def fetch_followers(username: str, fetch_count: int = 100000) -> list[str]:
    """
    Fetch followers of a public Instagram account via Apify.
    fetch_count controls how many followers to request from the API.
    Returns a list of follower usernames.
    """
    token = os.getenv("APIFY_API_TOKEN")
    if not token:
        raise RuntimeError("APIFY_API_TOKEN must be set in .env")

    client = ApifyClient(token)

    run_input = {
        "usernames": [username],
        "max_count": max(fetch_count, 50),
    }

    print(f"  Fetching followers for @{username} via Apify …")
    run = client.actor(FOLLOWERS_ACTOR_ID).call(run_input=run_input, timeout_secs=300)

    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())

    if not items:
        raise ProfileNotFoundError(
            f"No followers returned for @{username}. "
            f"The profile may not exist, be private, or have no followers."
        )

    # Extract usernames from follower items
    usernames = []
    for item in items:
        uname = item.get("username") or item.get("id") or item.get("login")
        if uname:
            usernames.append(uname)

    return usernames


# ---------------------------------------------------------------------------
# 2c. Processed-username tracking for resumable scans
# ---------------------------------------------------------------------------

PROCESSED_DIR = "processed_logs"


def _get_processed_path(target_username: str) -> str:
    """Return the path to the processed-log file for a target account."""
    return os.path.join(PROCESSED_DIR, f"processed_{target_username}.txt")


def load_processed(target_username: str) -> set[str]:
    """Load the set of already-processed follower usernames for a target account."""
    path = _get_processed_path(target_username)
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def mark_processed(target_username: str, follower_username: str):
    """Append a follower username to the processed-log file."""
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    path = _get_processed_path(target_username)
    with open(path, "a", encoding="utf-8") as f:
        f.write(follower_username + "\n")


# ---------------------------------------------------------------------------
# 3. Engagement metrics (calculated from Apify's recent posts data)
# ---------------------------------------------------------------------------

MAX_POSTS = 8  # use up to 8 recent posts for engagement calculation


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
    "Analyze this Instagram profile picture of a woman. Return a JSON object with exactly "
    "these fields:\n"
    '  "estimated_age": (int — single best-guess estimated age, must be 18+),\n'
    '  "ethnicity": (string — "white", "black", "asian"). Note that "white" includes Hispanics, Europeans, etc.,\n'
    '  "hair_color": (string — e.g. "black", "blonde", "brown", "red", "gray", "unknown"),\n'
    '  "attractiveness_rating": (float 0.0 to 1.0 — overall attractiveness scored '
    "on a roughly normal distribution. Follow this calibration strictly:\n"
    "    0.00–0.15: very unattractive (bottom ~5%)\n"
    "    0.15–0.30: below average\n"
    "    0.30–0.45: slightly below average\n"
    "    0.45–0.55: average — most people cluster here\n"
    "    0.55–0.70: above average, noticeably attractive\n"
    "    0.70–0.80: very attractive — would stand out in a crowd\n"
    "    0.80–0.90: strikingly beautiful — model-tier\n"
    "    0.90–1.00: exceptionally rare, top-tier beauty\n"
    "  Be harsh and discriminating. DO NOT default to the 0.6–0.8 range for everyone. "
    "Use the full range of scores to give specific values. "
    "Carefully evaluate facial symmetry, bone structure, skin clarity, eye shape, "
    "lip fullness, hair quality, body appeal, and overall harmony of features. "
    "Rate from the perspective of a young man.)\n"
    "\n"
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
                                "detail": "high",
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
            "estimated_age": result.get("estimated_age"),
            "ethnicity": result.get("ethnicity"),
            "hair_color": result.get("hair_color"),
            "attractiveness_rating": result.get("attractiveness_rating"),
        }

    except Exception as e:
        print(f"  ⚠ Vision analysis failed: {e}")
        return _empty_vision_result()


def _empty_vision_result() -> dict:
    return {
        "estimated_age": None,
        "ethnicity": None,
        "hair_color": None,
        "attractiveness_rating": None,
    }


# ---------------------------------------------------------------------------
# 4b. Username precheck via Grok (text-only, no image)
# ---------------------------------------------------------------------------

USERNAME_PRECHECK_PROMPT = (
    "You are analyzing an Instagram username to determine if it belongs to a real person "
    "or a business/organization/brand.\n\n"
    "Username: \"{username}\"\n\n"
    "Return a JSON object with exactly one field:\n"
    '  "is_personal": (boolean — true if this username likely belongs to a real person, '
    "false if it sounds like a business, organization, brand, group, store, agency, "
    "clinic, team, media outlet, etc.)\n\n"
    "Important: Many real people use underscores, dots, numbers, and abbreviations in "
    "their usernames. A username like 'jessica_m.02' or 'j.smith_97' is clearly personal. "
    "Only flag usernames that sound distinctly organizational or commercial "
    '(e.g. "alliancepsychologygroup", "downtowndentalcare", "thefitnessstudiola").\n'
    "When in doubt, lean toward is_personal: true.\n\n"
    "Return ONLY valid JSON, no explanation."
)


def precheck_username(username: str) -> bool:
    """
    Lightweight text-only check — determines if a username sounds like
    a real person or a business/organization.
    Returns True if likely personal, False if likely business.
    """
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        print("  ⚠ No XAI_API_KEY found — skipping username precheck.")
        return True  # default to allowing through

    try:
        client = OpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1",
        )

        prompt = USERNAME_PRECHECK_PROMPT.replace("{username}", username)

        response = client.chat.completions.create(
            model="grok-4.20-0309-non-reasoning",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        result = json.loads(raw)
        return result.get("is_personal", True)

    except Exception as e:
        print(f"  ⚠ Username precheck failed: {e}")
        return True  # default to allowing through


# ---------------------------------------------------------------------------
# 4c. Bio precheck via Grok (text-only, no image)
# ---------------------------------------------------------------------------

BIO_PRECHECK_PROMPT = (
    "You are analyzing an Instagram bio to determine if the person shows any signs "
    "that would make them ineligible for a romantic partnership.\n\n"
    "Bio: \"{bio}\"\n\n"
    "Return a JSON object with exactly these fields:\n"
    '  "is_parent": (boolean — true if the bio indicates they are a parent, '
    'e.g. mentions kids, "mom", "mama", "mother", child ages, etc.),\n'
    '  "in_relationship": (boolean — true if the bio indicates they are in a '
    'relationship, e.g. mentions husband, wife, boyfriend, girlfriend, partner, '
    '"taken", "wifey", wedding date, couple references, etc.),\n'
    '  "too_old": (boolean — true if the bio contains signals suggesting they are '
    "significantly older than mid-30s, e.g. mentions grandchildren, retirement, "
    "class of '85, etc.),\n"
    '  "other_disqualifier": (boolean — true if there are any other clear signals '
    "that would make them ineligible for a romantic partnership, e.g. memorial "
    "account, business-only account, explicitly not looking for dating, etc.)\n\n"
    "Important: Only flag signals that are clearly present in the bio text. "
    "If the bio is empty, ambiguous, or has no disqualifying signals, all fields "
    "should be false.\n\n"
    "Return ONLY valid JSON, no explanation."
)


def precheck_bio(bio: str) -> dict:
    """
    Lightweight text-only check — determines if a user's bio contains
    signals that would disqualify them (parent, in relationship, too old, etc.).
    Returns a dict with boolean fields for each disqualifier.
    """
    default = {
        "is_parent": False,
        "in_relationship": False,
        "too_old": False,
        "other_disqualifier": False,
    }

    if not bio or not bio.strip():
        return default

    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        print("  ⚠ No XAI_API_KEY found — skipping bio precheck.")
        return default

    try:
        client = OpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1",
        )

        prompt = BIO_PRECHECK_PROMPT.replace("{bio}", bio)

        response = client.chat.completions.create(
            model="grok-4.20-0309-non-reasoning",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        result = json.loads(raw)

        return {
            "is_parent": result.get("is_parent", False),
            "in_relationship": result.get("in_relationship", False),
            "too_old": result.get("too_old", False),
            "other_disqualifier": result.get("other_disqualifier", False),
        }

    except Exception as e:
        print(f"  ⚠ Bio precheck failed: {e}")
        return default


# ---------------------------------------------------------------------------
# 4d. Lightweight vision precheck for follower qualification
# ---------------------------------------------------------------------------

PRECHECK_VISION_PROMPT = (
    "Analyze this Instagram profile picture. Return a JSON object with exactly "
    "these fields:\n"
    '  "visible_person": (boolean — true ONLY if the image clearly shows exactly ONE woman\'s face or body, with no other people present),\n'
    '  "gender": (string — "male", "female", or "unknown"),\n'
    '  "estimated_age": (int — best-guess age, or 0 if no person visible),\n'
    '  "disqualify_reason": (string or null — reason the person should be disqualified, or null if none)\n'
    "\n"
    "Set visible_person to false if:\n"
    "  - The image does not clearly show a single person (e.g. logo, pet, object, default avatar, symbol)\n"
    "  - There are multiple people in the image (e.g. a couple, group photo, woman with a man)\n"
    "  - There is a child, baby, or infant in the image (likely a parent)\n"
    "\n"
    "Set disqualify_reason to a short description if you detect any of these red flags:\n"
    '  - Woman pictured with a man (e.g. "woman with man, likely in relationship")\n'
    '  - Child or baby present (e.g. "child in photo, likely a parent")\n'
    '  - Multiple people in photo (e.g. "group photo, not a solo picture")\n'
    '  - Wedding ring or wedding attire visible (e.g. "wedding imagery, likely married")\n'
    '  - Any other signal suggesting the person is in a relationship, is a parent, or is outside the 20-32 age range\n'
    "  - If none of these apply, set disqualify_reason to null\n"
    "\n"
    "The ideal profile picture is one clear photo of a single young woman (face or body). "
    "Anything else should be flagged.\n"
    "Return ONLY valid JSON, no explanation."
)


def precheck_profile_pic(profile_pic_url: str) -> dict:
    """
    Lightweight vision check — only determines visible_person, gender,
    and estimated_age to qualify/disqualify a follower before full analysis.
    """
    default = {"visible_person": False, "gender": "unknown", "estimated_age": 0, "disqualify_reason": None}

    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        print("  ⚠ No XAI_API_KEY found — skipping vision precheck.")
        return default

    try:
        resp = req.get(profile_pic_url, timeout=15)
        resp.raise_for_status()
        image_bytes = resp.content
    except req.RequestException as e:
        print(f"  ⚠ Failed to download profile picture: {e}")
        return default

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
                        {"type": "text", "text": PRECHECK_VISION_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64_image}",
                                "detail": "high",
                            },
                        },
                    ],
                }
            ],
            max_tokens=100,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        result = json.loads(raw)

        return {
            "visible_person": result.get("visible_person", False),
            "gender": result.get("gender", "unknown"),
            "estimated_age": result.get("estimated_age", 0),
            "disqualify_reason": result.get("disqualify_reason"),
        }

    except Exception as e:
        print(f"  ⚠ Vision precheck failed: {e}")
        return default


# ---------------------------------------------------------------------------
# 4e. Follower qualification checks
# ---------------------------------------------------------------------------

def qualify_follower_basic(profile_data: dict, raw_apify_data: dict) -> tuple:
    """
    Run free qualification checks (no API calls needed).
    Returns (passed: bool, reason: str).
    """
    followers = profile_data.get("followers", 0)
    if followers < 200:
        return False, f"too few followers ({followers})"
    if followers > 20000:
        return False, f"too many followers ({followers})"

    if raw_apify_data.get("isBusinessAccount", False):
        return False, "business account"
    if raw_apify_data.get("isProfessionalAccount", False):
        return False, "professional/creator account"

    return True, ""


def qualify_follower_vision(precheck_result: dict) -> tuple:
    """
    Run vision-based qualification checks using precheck results.
    Returns (passed: bool, reason: str).
    """
    if not precheck_result.get("visible_person", False):
        return False, "no visible person in profile picture"

    gender = precheck_result.get("gender", "unknown")
    if gender != "female":
        return False, f"gender is {gender}, not female"

    age = precheck_result.get("estimated_age", 0)
    if age < 20 or age > 32:
        return False, f"estimated age {age} outside 20-32 range"

    disqualify_reason = precheck_result.get("disqualify_reason")
    if disqualify_reason:
        return False, f"vision disqualified: {disqualify_reason}"

    return True, ""


# ---------------------------------------------------------------------------
# 5. Profile picture storage (Supabase Storage)
# ---------------------------------------------------------------------------

STORAGE_BUCKET = "profile-pics"


def get_instagram_pic_url(raw_apify_data: dict) -> str | None:
    """Extract the best profile picture URL from raw Apify data."""
    return raw_apify_data.get("profilePicUrlHD") or raw_apify_data.get("profilePicUrl")


def upload_profile_pic(username: str, profile_pic_url: str):
    """Download profile picture from Instagram and upload to Supabase Storage."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        print("  ⚠ SUPABASE_URL / SUPABASE_KEY not set — skipping pic upload.")
        return

    # Download image from Instagram
    try:
        resp = req.get(profile_pic_url, timeout=15)
        resp.raise_for_status()
        image_bytes = resp.content
    except req.RequestException as e:
        print(f"  ⚠ Failed to download profile picture for upload: {e}")
        return

    # Upload to Supabase Storage (overwrite if exists)
    try:
        supabase = create_client(url, key)
        file_path = f"{username}.jpg"

        supabase.storage.from_(STORAGE_BUCKET).upload(
            path=file_path,
            file=image_bytes,
            file_options={"content-type": "image/jpeg", "upsert": "true"},
        )
        print(f"  ✓ Profile pic uploaded to storage: {file_path}")
    except Exception as e:
        print(f"  ⚠ Storage upload failed: {e}")


def get_storage_pic_url(username: str) -> str:
    """Construct the public Supabase Storage URL for a profile picture."""
    supabase_url = os.getenv("SUPABASE_URL", "")
    return f"{supabase_url}/storage/v1/object/public/{STORAGE_BUCKET}/{username}.jpg"


# ---------------------------------------------------------------------------
# 6. Supabase write
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
