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

// ---------------------------------------------------------------------------
// Admin / Recommendation backend API
// ---------------------------------------------------------------------------

export interface City {
  id: string;
  name: string;
  state: string;
  country: string;
  lat?: number | null;
  lng?: number | null;
  status: "seeding" | "active";
  created_at?: string;
}

export interface Anchor {
  id: string;
  city_id: string;
  username: string;
  display_name?: string;
  category?: string;
  follower_count?: number;
  status: "pending" | "in_progress" | "done" | "invalid";
  invalid_reason?: string;
  followers_fetched_at?: string;
}

export interface AdminUser {
  id: string;
  email?: string;
  name?: string;
  city_id?: string;
  lat?: number | null;
  lng?: number | null;
  first_eligible_drop_at?: string;
  created_at?: string;
}

export interface AdminState {
  cities_total: number;
  cities_active: number;
  anchors_pending: number;
  anchors_done: number;
  anchors_invalid: number;
  profiles_qualified: number;
  profiles_disqualified: number;
  queue_queued: number;
  queue_in_progress: number;
  queue_failed: number;
  users: number;
}

export interface SchemaCheck {
  schema: Record<string, boolean>;
  ready: boolean;
}

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

export const adminApi = {
  schemaCheck: () => jsonFetch<SchemaCheck>("/api/admin/schema_check"),
  state: () => jsonFetch<AdminState>("/api/admin/state"),

  listCities: () => jsonFetch<{ cities: City[] }>("/api/admin/cities"),
  createCity: (body: {
    name: string; state: string; country?: string;
    lat?: number; lng?: number;
  }) =>
    jsonFetch<{ city: City }>("/api/admin/cities", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  seedCity: (cityId: string, maxAnchors?: number) => {
    const qs = maxAnchors !== undefined && maxAnchors > 0
      ? `?max_anchors=${maxAnchors}`
      : "";
    return jsonFetch<{ stats: Record<string, number> }>(
      `/api/admin/cities/${cityId}/seed${qs}`,
      { method: "POST" }
    );
  },
  listAnchors: (cityId: string) =>
    jsonFetch<{ anchors: Anchor[] }>(`/api/admin/cities/${cityId}/anchors`),

  listUsers: () => jsonFetch<{ users: AdminUser[] }>("/api/admin/users"),
  createUser: (body: {
    email: string; name?: string; city_id: string;
    lat?: number; lng?: number;
  }) =>
    jsonFetch<{ user: AdminUser }>("/api/admin/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  userRecommendations: (userId: string) =>
    jsonFetch<{
      recommendations: Array<{
        user_id: string;
        profile_username: string;
        drop_date: string;
        rank: number;
        profile?: Partial<Profile> & { analysis_state?: string };
        profile_image_url?: string;
      }>;
      available_now: number | null;
    }>(`/api/admin/users/${userId}/recommendations`),

  runDropPrep: () =>
    jsonFetch<{ stats: Record<string, number> }>("/api/admin/drop_prep", {
      method: "POST",
    }),
  runDrop: () =>
    jsonFetch<{ stats: Record<string, number> }>("/api/admin/run_drop", {
      method: "POST",
    }),
};

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
