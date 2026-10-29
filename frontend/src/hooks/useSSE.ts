import { useState, useEffect, useCallback, useRef } from "react";

export interface ScanEvent {
  event: string;
  message?: string;
  current?: number;
  total?: number;
  username?: string;
  status?: string;
  reason?: string;
  step?: string;
  stats?: {
    qualified?: number;
    disqualified?: number;
    errors?: number;
    matched?: number;
    scanned?: number;
  };
  data?: Record<string, unknown>;
  // keyword-scan "match" event fields
  name?: string | null;
  bio?: string | null;
  followers?: number | null;
  following?: number | null;
  is_private?: boolean;
  profile_url?: string;
  profile_image_url?: string | null;
}

export function useSSE(scanId: string | null, basePath: string = "/api/scan") {
  const [events, setEvents] = useState<ScanEvent[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [isComplete, setIsComplete] = useState(false);
  const [latestStats, setLatestStats] = useState<ScanEvent["stats"]>();
  const [progress, setProgress] = useState<{ current: number; total: number }>({ current: 0, total: 0 });
  const readerRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const disconnect = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    readerRef.current = null;
    setIsConnected(false);
  }, []);

  const reset = useCallback(() => {
    setEvents([]);
    setIsComplete(false);
    setLatestStats(undefined);
    setProgress({ current: 0, total: 0 });
  }, []);

  useEffect(() => {
    if (!scanId) return;

    const abortController = new AbortController();
    abortRef.current = abortController;
    setIsConnected(true);

    const connect = async () => {
      try {
        const response = await fetch(`${basePath}/${scanId}/stream`, {
          signal: abortController.signal,
        });

        if (!response.ok || !response.body) {
          setIsConnected(false);
          return;
        }

        const reader = response.body.getReader();
        readerRef.current = reader;
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed || trimmed.startsWith(":")) continue;
            if (trimmed.startsWith("data: ")) {
              try {
                const parsed: ScanEvent = JSON.parse(trimmed.slice(6));
                setEvents((prev) => [...prev, parsed]);

                if (parsed.stats) setLatestStats(parsed.stats);
                if (parsed.current !== undefined && parsed.total !== undefined) {
                  setProgress({ current: parsed.current, total: parsed.total });
                }
                if (parsed.event === "scan_started" && parsed.total) {
                  setProgress({ current: 0, total: parsed.total });
                }
                if (parsed.event === "complete") {
                  setIsComplete(true);
                  setIsConnected(false);
                }
              } catch {
                // skip malformed
              }
            }
          }
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        console.error("SSE error:", err);
      } finally {
        setIsConnected(false);
      }
    };

    connect();
    return () => disconnect();
  }, [scanId, basePath, disconnect]);

  return { events, isConnected, isComplete, latestStats, progress, disconnect, reset };
}
