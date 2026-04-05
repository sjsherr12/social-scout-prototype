import { useState, useRef, useEffect } from "react";
import {
  Users,
  Loader2,
  AlertCircle,
  Square,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Play,
} from "lucide-react";
import { startScan, stopScan } from "../lib/api";
import { useSSE } from "../hooks/useSSE";
import type { ScanEvent } from "../hooks/useSSE";

function EventRow({ event }: { event: ScanEvent }) {
  if (event.event === "follower_result") {
    const isQualified = event.status === "qualified";
    const isError = event.status === "error";
    return (
      <div className="flex items-center gap-2 py-1 text-sm">
        {isQualified ? (
          <CheckCircle2 size={14} className="text-green-400 shrink-0" />
        ) : isError ? (
          <AlertTriangle size={14} className="text-yellow-400 shrink-0" />
        ) : (
          <XCircle size={14} className="text-zinc-500 shrink-0" />
        )}
        <span
          className={
            isQualified
              ? "text-green-400 font-medium"
              : isError
                ? "text-yellow-400"
                : "text-zinc-500"
          }
        >
          @{event.username}
        </span>
        <span className="text-zinc-600 text-xs truncate">{event.reason}</span>
      </div>
    );
  }

  if (event.event === "follower_step") {
    return (
      <div className="flex items-center gap-2 py-0.5 text-xs text-zinc-600">
        <Loader2 size={12} className="animate-spin shrink-0" />
        <span>@{event.username}</span>
        <span className="text-zinc-700">{event.step}</span>
      </div>
    );
  }

  if (event.event === "info" || event.event === "status") {
    return (
      <div className="py-1 text-xs text-zinc-500">{event.message}</div>
    );
  }

  if (event.event === "error") {
    return (
      <div className="flex items-center gap-2 py-1 text-sm text-red-400">
        <AlertCircle size={14} className="shrink-0" />
        {event.message}
      </div>
    );
  }

  return null;
}

export default function FollowerScan() {
  const [username, setUsername] = useState("");
  const [maxFollowers, setMaxFollowers] = useState("");
  const [locationId, setLocationId] = useState("");
  const [locationSlug, setLocationSlug] = useState("");
  const [scanId, setScanId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  const { events, isConnected, isComplete, latestStats, progress, reset } =
    useSSE(scanId);

  // Auto-scroll log
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [events]);

  const handleStart = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim()) return;

    setStarting(true);
    setError(null);
    reset();

    try {
      const res = await startScan({
        username: username.trim(),
        max_followers: maxFollowers ? parseInt(maxFollowers) : undefined,
        location_id: locationId || undefined,
        location_slug: locationSlug || undefined,
      });
      setScanId(res.scan_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start scan");
    } finally {
      setStarting(false);
    }
  };

  const handleStop = async () => {
    if (scanId) {
      await stopScan(scanId);
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
        <h1 className="text-2xl font-bold text-white mb-2">Scan Followers</h1>
        <p className="text-zinc-400 text-sm">
          Scan followers of a target account, qualify each one through the
          pipeline, and save qualified profiles to the database.
        </p>
      </div>

      {/* Form */}
      {!scanId && (
        <form onSubmit={handleStart} className="mb-8">
          <div className="flex gap-3 mb-4">
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
            <input
              type="number"
              value={maxFollowers}
              onChange={(e) => setMaxFollowers(e.target.value)}
              placeholder="Max followers"
              min={1}
              className="w-36 bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2.5 text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:border-zinc-500 transition-colors"
              disabled={starting}
            />
            <button
              type="submit"
              disabled={starting || !username.trim()}
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
          <div className="flex gap-3">
            <input
              type="text"
              value={locationId}
              onChange={(e) => setLocationId(e.target.value)}
              placeholder="Location ID (optional)"
              className="flex-1 bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:border-zinc-500 transition-colors"
              disabled={starting}
            />
            <input
              type="text"
              value={locationSlug}
              onChange={(e) => setLocationSlug(e.target.value)}
              placeholder="Location slug (optional)"
              className="flex-1 bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:border-zinc-500 transition-colors"
              disabled={starting}
            />
          </div>
        </form>
      )}

      {error && (
        <div className="bg-red-950/50 border border-red-800 rounded-lg p-4 mb-6 flex items-start gap-3">
          <AlertCircle size={18} className="text-red-400 mt-0.5 shrink-0" />
          <p className="text-red-300 text-sm">{error}</p>
        </div>
      )}

      {/* Progress & Stats */}
      {scanId && (
        <div className="space-y-4">
          {/* Header bar */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              {isRunning && (
                <div className="flex items-center gap-2 text-sm text-blue-400">
                  <Loader2 size={16} className="animate-spin" />
                  Scanning @{username}...
                </div>
              )}
              {isDone && (
                <div className="flex items-center gap-2 text-sm text-green-400">
                  <CheckCircle2 size={16} />
                  Scan complete
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
                  {progress.current} / {progress.total} followers
                </span>
                <span>
                  {Math.round((progress.current / progress.total) * 100)}%
                </span>
              </div>
              <div className="h-2 bg-zinc-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-blue-500 rounded-full transition-all duration-300"
                  style={{
                    width: `${(progress.current / progress.total) * 100}%`,
                  }}
                />
              </div>
            </div>
          )}

          {/* Stats badges */}
          {latestStats && (
            <div className="flex gap-3">
              <div className="flex items-center gap-2 bg-green-950/30 border border-green-900 rounded-lg px-3 py-2">
                <CheckCircle2 size={14} className="text-green-400" />
                <span className="text-sm text-green-400 font-medium">
                  {latestStats.qualified} qualified
                </span>
              </div>
              <div className="flex items-center gap-2 bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2">
                <XCircle size={14} className="text-zinc-500" />
                <span className="text-sm text-zinc-400">
                  {latestStats.disqualified} disqualified
                </span>
              </div>
              <div className="flex items-center gap-2 bg-yellow-950/30 border border-yellow-900 rounded-lg px-3 py-2">
                <AlertTriangle size={14} className="text-yellow-400" />
                <span className="text-sm text-yellow-400">
                  {latestStats.errors} errors
                </span>
              </div>
            </div>
          )}

          {/* Event log */}
          <div
            ref={logRef}
            className="bg-zinc-900 border border-zinc-800 rounded-xl p-4 max-h-[500px] overflow-y-auto"
          >
            {events.length === 0 ? (
              <p className="text-zinc-600 text-sm">Waiting for events...</p>
            ) : (
              events.map((event, i) => <EventRow key={i} event={event} />)
            )}
          </div>
        </div>
      )}
    </div>
  );
}
