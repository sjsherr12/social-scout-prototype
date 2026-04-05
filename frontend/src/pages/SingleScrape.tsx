import { useState } from "react";
import { Search, Loader2, AlertCircle } from "lucide-react";
import { scrapeProfile } from "../lib/api";
import type { Profile } from "../lib/api";
import ProfileCard from "../components/ProfileCard";

export default function SingleScrape() {
  const [username, setUsername] = useState("");
  const [skipVision, setSkipVision] = useState(false);
  const [skipEngagement, setSkipEngagement] = useState(false);
  const [locationId, setLocationId] = useState("");
  const [locationSlug, setLocationSlug] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Profile | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim()) return;

    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const res = await scrapeProfile({
        username: username.trim(),
        skip_vision: skipVision,
        skip_engagement: skipEngagement,
        location_id: locationId || undefined,
        location_slug: locationSlug || undefined,
      });
      setResult(res.data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scrape failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-white mb-2">Scrape Profile</h1>
        <p className="text-zinc-400 text-sm">
          Enter an Instagram username or URL to scrape profile data, engagement
          metrics, and run vision analysis.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="mb-8">
        {/* Username input */}
        <div className="flex gap-3 mb-4">
          <div className="flex-1 relative">
            <Search
              size={16}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500"
            />
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Instagram username or URL..."
              className="w-full bg-zinc-900 border border-zinc-700 rounded-lg pl-10 pr-4 py-2.5 text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:border-zinc-500 transition-colors"
              disabled={loading}
            />
          </div>
          <button
            type="submit"
            disabled={loading || !username.trim()}
            className="bg-white text-zinc-900 px-5 py-2.5 rounded-lg text-sm font-medium hover:bg-zinc-200 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
          >
            {loading ? (
              <>
                <Loader2 size={16} className="animate-spin" />
                Scraping...
              </>
            ) : (
              "Scrape"
            )}
          </button>
        </div>

        {/* Options */}
        <div className="flex flex-wrap gap-4 mb-4">
          <label className="flex items-center gap-2 text-sm text-zinc-400 cursor-pointer">
            <input
              type="checkbox"
              checked={skipVision}
              onChange={(e) => setSkipVision(e.target.checked)}
              className="rounded border-zinc-600 bg-zinc-800 accent-white"
              disabled={loading}
            />
            Skip Vision Analysis
          </label>
          <label className="flex items-center gap-2 text-sm text-zinc-400 cursor-pointer">
            <input
              type="checkbox"
              checked={skipEngagement}
              onChange={(e) => setSkipEngagement(e.target.checked)}
              className="rounded border-zinc-600 bg-zinc-800 accent-white"
              disabled={loading}
            />
            Skip Engagement
          </label>
        </div>

        {/* Location fields */}
        <div className="flex gap-3">
          <input
            type="text"
            value={locationId}
            onChange={(e) => setLocationId(e.target.value)}
            placeholder="Location ID (optional)"
            className="flex-1 bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:border-zinc-500 transition-colors"
            disabled={loading}
          />
          <input
            type="text"
            value={locationSlug}
            onChange={(e) => setLocationSlug(e.target.value)}
            placeholder="Location slug (optional)"
            className="flex-1 bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:border-zinc-500 transition-colors"
            disabled={loading}
          />
        </div>
      </form>

      {/* Error */}
      {error && (
        <div className="bg-red-950/50 border border-red-800 rounded-lg p-4 mb-6 flex items-start gap-3">
          <AlertCircle size={18} className="text-red-400 mt-0.5 shrink-0" />
          <p className="text-red-300 text-sm">{error}</p>
        </div>
      )}

      {/* Result */}
      {result && <ProfileCard profile={result} />}
    </div>
  );
}
