import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Search,
  ChevronLeft,
  ChevronRight,
  ArrowUpDown,
  Loader2,
  AlertCircle,
  ExternalLink,
  Sparkles,
} from "lucide-react";
import { fetchProfiles } from "../lib/api";
import type { Profile } from "../lib/api";

const SORT_OPTIONS = [
  { value: "last_scraped", label: "Last Scraped" },
  { value: "attractiveness_rating", label: "Attractiveness" },
  { value: "followers", label: "Followers" },
  { value: "engagement_rate", label: "Engagement Rate" },
  { value: "follower_ratio", label: "Ratio" },
  { value: "estimated_age", label: "Age" },
];

function ratingColor(rating: number): string {
  if (rating >= 0.8) return "text-green-400";
  if (rating >= 0.6) return "text-yellow-400";
  if (rating >= 0.4) return "text-orange-400";
  return "text-red-400";
}

function Avatar({ src, username }: { src?: string; username: string }) {
  const [failed, setFailed] = useState(false);
  const initial = username?.charAt(0).toUpperCase() ?? "?";

  if (!src || failed) {
    return (
      <div className="w-9 h-9 rounded-full bg-zinc-800 flex items-center justify-center text-zinc-600 text-xs font-medium shrink-0">
        {initial}
      </div>
    );
  }

  return (
    <img
      src={src}
      alt=""
      className="w-9 h-9 rounded-full object-cover border border-zinc-700 shrink-0"
      onError={() => setFailed(true)}
    />
  );
}

function ProfileRow({ profile }: { profile: Profile }) {
  return (
    <tr className="border-b border-zinc-800/50 hover:bg-zinc-800/30 transition-colors">
      {/* Avatar + Username */}
      <td className="py-3 px-3">
        <div className="flex items-center gap-3">
          <Avatar src={profile.profile_image_url} username={profile.username} />
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="text-sm font-medium text-white truncate">
                @{profile.username}
              </span>
              <a
                href={`https://www.instagram.com/${profile.username}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-zinc-600 hover:text-zinc-400 transition-colors"
              >
                <ExternalLink size={12} />
              </a>
            </div>
            {profile.name && (
              <p className="text-xs text-zinc-500 truncate">{profile.name}</p>
            )}
          </div>
        </div>
      </td>
      {/* Followers */}
      <td className="py-3 px-3 text-sm text-zinc-300 text-right">
        {profile.followers?.toLocaleString() ?? "-"}
      </td>
      {/* Engagement */}
      <td className="py-3 px-3 text-sm text-zinc-300 text-right">
        {profile.engagement_rate !== null && profile.engagement_rate !== undefined
          ? `${profile.engagement_rate}%`
          : "-"}
      </td>
      {/* Ratio */}
      <td className="py-3 px-3 text-sm text-zinc-300 text-right">
        {profile.follower_ratio !== null && profile.follower_ratio !== undefined
          ? profile.follower_ratio.toFixed(2)
          : "-"}
      </td>
      {/* Age */}
      <td className="py-3 px-3 text-sm text-zinc-300 text-center">
        {profile.estimated_age ? `~${profile.estimated_age}` : "-"}
      </td>
      {/* Ethnicity */}
      <td className="py-3 px-3 text-sm text-zinc-400 text-center">
        {profile.ethnicity ?? "-"}
      </td>
      {/* Rating */}
      <td className="py-3 px-3 text-right">
        {profile.attractiveness_rating !== null &&
        profile.attractiveness_rating !== undefined ? (
          <div className="flex items-center justify-end gap-1.5">
            <Sparkles size={12} className="text-zinc-600" />
            <span
              className={`text-sm font-bold ${ratingColor(profile.attractiveness_rating)}`}
            >
              {profile.attractiveness_rating.toFixed(2)}
            </span>
          </div>
        ) : (
          <span className="text-sm text-zinc-600">-</span>
        )}
      </td>
      {/* Location */}
      <td className="py-3 px-3 text-sm text-zinc-500 text-center">
        {profile.location_slug ?? "-"}
      </td>
      {/* Last Scraped */}
      <td className="py-3 px-3 text-xs text-zinc-600">
        {profile.last_scraped
          ? new Date(profile.last_scraped).toLocaleDateString()
          : "-"}
      </td>
    </tr>
  );
}

export default function Database() {
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState("last_scraped");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const perPage = 25;

  const { data, isLoading, error } = useQuery({
    queryKey: ["profiles", search, sortBy, sortDir, page],
    queryFn: () =>
      fetchProfiles({
        search: search || undefined,
        sort_by: sortBy,
        sort_dir: sortDir,
        page,
        per_page: perPage,
      }),
  });

  const totalPages = data ? Math.ceil(data.total / perPage) : 0;

  const toggleSort = (field: string) => {
    if (sortBy === field) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortBy(field);
      setSortDir("desc");
    }
    setPage(1);
  };

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white mb-2">Database</h1>
        <p className="text-zinc-400 text-sm">
          Browse and filter all saved profiles.
          {data && (
            <span className="text-zinc-500"> ({data.total} total profiles)</span>
          )}
        </p>
      </div>

      {/* Controls */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="flex-1 min-w-[200px] relative">
          <Search
            size={16}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500"
          />
          <input
            type="text"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            placeholder="Search username or name..."
            className="w-full bg-zinc-900 border border-zinc-700 rounded-lg pl-10 pr-4 py-2 text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:border-zinc-500 transition-colors"
          />
        </div>
        <div className="flex items-center gap-2">
          <ArrowUpDown size={14} className="text-zinc-500" />
          <select
            value={sortBy}
            onChange={(e) => {
              setSortBy(e.target.value);
              setPage(1);
            }}
            className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-zinc-500"
          >
            {SORT_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <button
            onClick={() => setSortDir((d) => (d === "asc" ? "desc" : "asc"))}
            className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-400 hover:text-white hover:border-zinc-500 transition-colors"
          >
            {sortDir === "asc" ? "ASC" : "DESC"}
          </button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-950/50 border border-red-800 rounded-lg p-4 mb-6 flex items-start gap-3">
          <AlertCircle size={18} className="text-red-400 mt-0.5 shrink-0" />
          <p className="text-red-300 text-sm">
            {error instanceof Error ? error.message : "Failed to load profiles"}
          </p>
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <div className="flex justify-center py-12">
          <Loader2 size={24} className="animate-spin text-zinc-500" />
        </div>
      )}

      {/* Table */}
      {data && (
        <>
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-zinc-800">
                    {[
                      { key: "username", label: "Profile", align: "text-left" },
                      { key: "followers", label: "Followers", align: "text-right" },
                      { key: "engagement_rate", label: "Engagement", align: "text-right" },
                      { key: "follower_ratio", label: "Ratio", align: "text-right" },
                      { key: "estimated_age", label: "Age", align: "text-center" },
                      { key: "ethnicity", label: "Ethnicity", align: "text-center" },
                      { key: "attractiveness_rating", label: "Rating", align: "text-right" },
                      { key: "location_slug", label: "Location", align: "text-center" },
                      { key: "last_scraped", label: "Scraped", align: "text-left" },
                    ].map((col) => (
                      <th
                        key={col.key}
                        onClick={() => toggleSort(col.key)}
                        className={`py-3 px-3 text-xs font-medium text-zinc-500 uppercase tracking-wider ${col.align} cursor-pointer hover:text-zinc-300 transition-colors select-none`}
                      >
                        <span className="inline-flex items-center gap-1">
                          {col.label}
                          {sortBy === col.key && (
                            <span className="text-zinc-400">
                              {sortDir === "asc" ? "^" : "v"}
                            </span>
                          )}
                        </span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.data.length === 0 ? (
                    <tr>
                      <td
                        colSpan={9}
                        className="py-12 text-center text-zinc-600 text-sm"
                      >
                        No profiles found.
                      </td>
                    </tr>
                  ) : (
                    data.data.map((profile) => (
                      <ProfileRow key={profile.username} profile={profile} />
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between mt-4">
              <p className="text-sm text-zinc-500">
                Page {page} of {totalPages}
              </p>
              <div className="flex gap-2">
                <button
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page === 1}
                  className="flex items-center gap-1 bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-1.5 text-sm text-zinc-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                >
                  <ChevronLeft size={14} />
                  Prev
                </button>
                <button
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page === totalPages}
                  className="flex items-center gap-1 bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-1.5 text-sm text-zinc-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                >
                  Next
                  <ChevronRight size={14} />
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
