# Social Scout

Social Scout is a prototype tool for finding and qualifying Instagram profiles. Give it a single profile to enrich, or point it at an account to work through that account's followers. Each follower is checked against a set of criteria, and the ones that pass are saved to a Supabase database you can search.

It has a Python pipeline (CLI and FastAPI server) and a React dashboard where you can start scrapes, watch follower scans live, and browse the results.

## Features

- **Single profile scrape**: pulls profile metadata (followers, following, bio, post count, privacy), works out engagement (average likes, average comments, engagement rate) from recent posts, and runs a vision analysis of the profile picture.
- **Follower scan**: fetches an account's followers and runs each one through a qualification pipeline that goes from cheap checks to expensive ones:
  1. Basic checks with no API cost: follower count (250–10,000), post count (1–200), and whether it's a business or creator account.
  2. An LLM check on the username, then on the bio, to filter out brands and organizations.
  3. A vision check on the profile picture: is a person visible, plus estimated gender and age.
- **Live progress**: scan status streams to the dashboard over Server-Sent Events, and you can stop a scan while it runs.
- **Resumable scans**: followers that have already been processed are logged per target account, so a rerun skips them.
- **Location tagging**: tag scraped profiles with a location cluster (by ID and slug) so you can group them by market.
- **Profile picture storage**: pictures are uploaded to Supabase Storage, so they still load after Instagram's CDN links expire. `backfill_pics.py` fills them in for profiles saved earlier.
- **Database browser**: search and filter saved profiles from the dashboard.

## Tech Stack

| Layer | Tools |
| --- | --- |
| Scraping | [Apify](https://apify.com) Instagram Profile Scraper |
| AI / Vision | xAI Grok (through the OpenAI-compatible API) |
| Backend | Python, FastAPI, Uvicorn |
| Database & storage | Supabase (`profiles` table, `profile-pics` bucket) |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS v4, TanStack Query, React Router |

## Project Structure

```
├── start.py           # Starts the backend and frontend together
├── server.py          # FastAPI app (scrape, scan, SSE stream, profiles API)
├── main.py            # CLI entry point
├── scraper.py         # Core logic: Apify, engagement, Grok checks, Supabase
├── backfill_pics.py   # Backfills profile pictures into Supabase Storage
├── requirements.txt
└── frontend/          # React + Vite dashboard
    └── src/pages/     # SingleScrape, FollowerScan, Database
```

## Getting Started

### Prerequisites

- Python 3.10+
- Node.js 18+
- Accounts for Apify, xAI, and Supabase

### Setup

1. Install the backend dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Create a `.env` file in the project root:

   ```env
   APIFY_API_TOKEN=your_apify_token
   XAI_API_KEY=your_xai_key
   SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_KEY=your_supabase_key
   ```

3. Start the app. This installs the frontend dependencies on the first run:

   ```bash
   python start.py
   ```

   - Backend: http://localhost:8000
   - Frontend: http://localhost:3000

### CLI Usage

```bash
python main.py natgeo                                   # full scrape of one profile
python main.py natgeo --only followers,following        # update specific fields only
python main.py natgeo --skip-vision                     # skip the profile picture analysis
python main.py natgeo --scan-followers --max-followers 500
python main.py natgeo --scan-followers --location-slug austin-tx
```

Run `python main.py --help` to see every option.

## API

| Method | Endpoint | Description |
| --- | --- | --- |
| `POST` | `/api/scrape` | Scrape a single profile |
| `POST` | `/api/scan` | Start a follower scan |
| `GET` | `/api/scan/{scan_id}/stream` | Stream scan progress (SSE) |
| `POST` | `/api/scan/{scan_id}/stop` | Stop a running scan |
| `GET` | `/api/profiles` | List or search saved profiles |
| `GET` | `/api/profiles/{username}` | Get one profile |

## Notes

- This is a prototype. The qualification thresholds are hard-coded in `scraper.py` (`qualify_follower_basic` and `qualify_follower_vision`). Change them there to fit your use case.
- Apify and xAI bill per call. Scans run the free checks first so fewer profiles reach the paid ones.
- Collecting data from Instagram is subject to Instagram's Terms of Service and to privacy laws where you operate. Make sure your use complies.
