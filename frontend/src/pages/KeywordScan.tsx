import { useState } from "react";
import {
  Users,
  Tag,
  Loader2,
  AlertCircle,
  Square,
  CheckCircle2,
  Play,
  ExternalLink,
} from "lucide-react";
import { startKeywordScan, stopKeywordScan } from "../lib/api";
import { useSSE } from "../hooks/useSSE";
import type { ScanEvent } from "../hooks/useSSE";

function Avatar({ src, username }: { src?: string | null; username: string }) {
  const [failed, setFailed] = useState(false);
  const initial = username?.charAt(0).toUpperCase() ?? "?";

  if (!src || failed) {
    return (
      <div className="w-10 h-10 rounded-full bg-zinc-800 flex items-center justify-center text-zinc-600 text-sm font-medium shrink-0">
        {initial}
      </div>
    );
  }

  return (
    <img
      src={src}
      alt=""
      referrerPolicy="no-referrer"
      className="w-10 h-10 rounded-full object-cover border border-zinc-700 shrink-0"
      onError={() => setFailed(true)}
    />
  );
}

/** Render a bio with every case-insensitive occurrence of `keyword` highlighted. */
function HighlightedBio({ bio, keyword }: { bio: string; keyword: string }) {
  if (!keyword) return <>{bio}</>;
  // Escape regex special chars in the keyword
  const escaped = keyword.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const parts = bio.split(new RegExp(`(${escaped})`, "gi"));
  return (
    <>
      {parts.map((part, i) =>
        part.toLowerCase() === keyword.toLowerCase() ? (
          <mark key={i} className="bg-yellow-400/30 text-yellow-200 rounded px-0.5">
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

function MatchCard({ match, keyword }: { match: ScanEvent; keyword: string }) {
  return (
    <a
      href={match.profile_url}
      target="_blank"
      rel="noopener noreferrer"
      className="block bg-zinc-900 border border-zinc-800 rounded-xl p-4 hover:border-zinc-600 transition-colors group"
    >
      <div className="flex items-start gap-3">
        <Avatar src={match.profile_image_url} username={match.username ?? ""} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="text-sm font-semibold text-white truncate">
              @{match.username}
            </span>
            <ExternalLink
              size={12}
              className="text-zinc-600 group-hover:text-zinc-300 transition-colors shrink-0"
            />
          </div>
          {match.name && (
            <p className="text-xs text-zinc-500 truncate">{match.name}</p>
          )}
          <div className="mt-1 flex items-center gap-3 text-xs text-zinc-500">
            {match.followers !== null && match.followers !== undefined && (
              <span>{match.followers.toLocaleString()} followers</span>
            )}
            {match.is_private && <span className="text-zinc-600">private</span>}
          </div>
        </div>
      </div>
      {match.bio && (
        <p className="mt-3 text-xs text-zinc-400 whitespace-pre-wrap break-words line-clamp-4">
          <HighlightedBio bio={match.bio} keyword={keyword} />
        </p>
      )}
    </a>
  );
}

export default function KeywordScan() {
  const [username, setUsername] = useState("");
  const [keyword, setKeyword] = useState("");
  const [maxFollowers, setMaxFollowers] = useState("");
  const [scanId, setScanId] = useState<string | null>(null);
  const [activeKeyword, setActiveKeyword] = useState("");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { events, isConnected, isComplete, latestStats, progress, reset } =
    useSSE(scanId, "/api/keyword-scan");

  const matches = events.filter((e) => e.event === "match");
  const matchedCount = latestStats?.matched ?? matches.length;
  const scannedCount = latestStats?.scanned ?? progress.current;

  const handleStart = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !keyword.trim()) return;

    setStarting(true);
    setError(null);
    reset();

    try {
      const res = await startKeywordScan({
        username: username.trim(),
        keyword: keyword.trim(),
        max_followers: maxFollowers ? parseInt(maxFollowers) : undefined,
      });
      setActiveKeyword(res.keyword);
      setScanId(res.scan_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start scan");
    } finally {
      setStarting(false);
    }
  };

  const handleStop = async () => {
    if (scanId) {
      await stopKeywordScan(scanId);
    }
  };

  const handleNewScan = () => {
    setScanId(null);
    reset();
    setError(null);
  };

  const isRunning = scanId && isConnected && !isComplete;
  const isDone = scanId && isComplete;

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-white mb-2">Keyword Bio Scan</h1>
        <p className="text-zinc-400 text-sm">
          Scan every follower of a target account and collect the ones whose bio
          contains a keyword. Standalone — no qualification, vision, or database
          filtering. Matches appear below with links to each profile.
        </p>
      </div>

      {/* Form */}
      {!scanId && (
        <form onSubmit={handleStart} className="mb-8 space-y-3">
          <div className="flex gap-3">
            <div className="flex-1 relative">
              <Users
                size={16}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500"
              />
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Target account username..."
                className="w-full bg-zinc-900 border border-zinc-700 rounded-lg pl-10 pr-4 py-2.5 text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:border-zinc-500 transition-colors"
                disabled={starting}
              />
            </div>
            <div className="flex-1 relative">
              <Tag
                size={16}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500"
              />
              <input
                type="text"
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                placeholder="Keyword to find in bios..."
                className="w-full bg-zinc-900 border border-zinc-700 rounded-lg pl-10 pr-4 py-2.5 text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:border-zinc-500 transition-colors"
                disabled={starting}
              />
            </div>
            <input
              type="number"
              value={maxFollowers}
              onChange={(e) => setMaxFollowers(e.target.value)}
              placeholder="Max"
              min={1}
              title="Max followers to scan (optional)"
              className="w-28 bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2.5 text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:border-zinc-500 transition-colors"
              disabled={starting}
            />
            <button
              type="submit"
              disabled={starting || !username.trim() || !keyword.trim()}
              className="bg-white text-zinc-900 px-5 py-2.5 rounded-lg text-sm font-medium hover:bg-zinc-200 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
            >
              {starting ? (
                <>
                  <Loader2 size={16} className="animate-spin" />
                  Starting...
                </>
              ) : (
                <>
                  <Play size={16} />
                  Start Scan
                </>
              )}
            </button>
          </div>
        </form>
      )}

      {error && (
        <div className="bg-red-950/50 border border-red-800 rounded-lg p-4 mb-6 flex items-start gap-3">
          <AlertCircle size={18} className="text-red-400 mt-0.5 shrink-0" />
          <p className="text-red-300 text-sm">{error}</p>
        </div>
      )}

      {/* Progress & Results */}
      {scanId && (
        <div className="space-y-4">
          {/* Header bar */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              {isRunning && (
                <div className="flex items-center gap-2 text-sm text-blue-400">
                  <Loader2 size={16} className="animate-spin" />
                  Scanning @{username} for "{activeKeyword}"...
                </div>
              )}
              {isDone && (
                <div className="flex items-center gap-2 text-sm text-green-400">
                  <CheckCircle2 size={16} />
                  Scan complete — {matchedCount} match
                  {matchedCount === 1 ? "" : "es"} for "{activeKeyword}"
                </div>
              )}
            </div>
            <div className="flex gap-2">
              {isRunning && (
                <button
                  onClick={handleStop}
                  className="flex items-center gap-2 bg-red-900/50 text-red-300 border border-red-800 px-3 py-1.5 rounded-lg text-sm hover:bg-red-900 transition-colors"
                >
                  <Square size={14} />
                  Stop
                </button>
              )}
              {isDone && (
                <button
                  onClick={handleNewScan}
                  className="flex items-center gap-2 bg-zinc-800 text-zinc-300 px-3 py-1.5 rounded-lg text-sm hover:bg-zinc-700 transition-colors"
                >
                  New Scan
                </button>
              )}
            </div>
          </div>

          {/* Progress bar */}
          {progress.total > 0 && (
            <div>
              <div className="flex justify-between text-xs text-zinc-500 mb-1">
                <span>
                  {scannedCount} / {progress.total} followers scanned
                </span>
                <span>
                  {Math.round((scannedCount / progress.total) * 100)}%
                </span>
              </div>
              <div className="h-2 bg-zinc-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-blue-500 rounded-full transition-all duration-300"
                  style={{
                    width: `${(scannedCount / progress.total) * 100}%`,
                  }}
                />
              </div>
            </div>
          )}

          {/* Match count badge */}
          <div className="flex gap-3">
            <div className="flex items-center gap-2 bg-yellow-950/30 border border-yellow-900 rounded-lg px-3 py-2">
              <Tag size={14} className="text-yellow-400" />
              <span className="text-sm text-yellow-400 font-medium">
                {matchedCount} bio match{matchedCount === 1 ? "" : "es"}
              </span>
            </div>
          </div>

          {/* Results grid */}
          {matches.length === 0 ? (
            <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-8 text-center text-zinc-600 text-sm">
              {isRunning
                ? "Scanning followers... matches will appear here as they're found."
                : "No bios matched that keyword."}
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {matches.map((match, i) => (
                <MatchCard key={`${match.username}-${i}`} match={match} keyword={activeKeyword} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
