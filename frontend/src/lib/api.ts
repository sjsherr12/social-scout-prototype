// API helper functions

export interface Profile {
  username: string;

  name?: string;
  followers?: number;
  following?: number;
  bio?: string;
  profile_image_url?: string;
  is_private?: boolean;
  post_count?: number;
  avg_likes?: number;
  avg_comments?: number;
  engagement_rate?: number;
  estimated_age?: number;
  ethnicity?: string;
  hair_color?: string;
  attractiveness_rating?: number;
  location_cluster_id?: string;
  location_slug?: string;
  last_scraped?: string;
}

export interface ScrapeRequest {
  username: string;
  skip_vision: boolean;
  skip_engagement: boolean;
  only_fields?: string[];
  location_id?: string;
  location_slug?: string;
}

export interface ScanRequest {
  username: string;
  max_followers?: number;
  location_id?: string;
  location_slug?: string;
}

export interface ProfilesResponse {
  data: Profile[];
  total: number;
  page: number;
  per_page: number;
}

export async function scrapeProfile(req: ScrapeRequest): Promise<{ status: string; data: Profile }> {
  const res = await fetch("/api/scrape", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Scrape failed");
  }
  return res.json();
}

export async function startScan(req: ScanRequest): Promise<{ scan_id: string; username: string }> {
  const res = await fetch("/api/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Scan start failed");
  }
  return res.json();
}

export async function stopScan(scanId: string): Promise<void> {
  await fetch(`/api/scan/${scanId}/stop`, { method: "POST" });
}

export async function fetchProfiles(params: {
  search?: string;
  sort_by?: string;
  sort_dir?: string;
  min_followers?: number;
  max_followers?: number;
  location_slug?: string;
  page?: number;
  per_page?: number;
}): Promise<ProfilesResponse> {
  const searchParams = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      searchParams.set(key, String(value));
    }
  }
  const res = await fetch(`/api/profiles?${searchParams}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to fetch profiles");
  }
  return res.json();
}
