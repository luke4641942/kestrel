import { useCallback, useEffect, useRef, useState } from "react";

export interface Resource<T> {
  data: T | undefined;
  error: string | undefined;
  loading: boolean;
  reload: () => void;
}

/** Fetch on mount and whenever deps change; optionally re-poll every `intervalMs`, and
 * with `refetchOnFocus` fetch again (quietly, keeping what is shown) when the browser tab
 * comes back into view: notes and runs change from the CLI while the page sits open. */
export function useResource<T>(
  fetcher: () => Promise<T>,
  deps: unknown[],
  intervalMs?: number,
  refetchOnFocus = false,
): Resource<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    let alive = true;
    const run = () =>
      fetcherRef
        .current()
        .then((d) => alive && (setData(d), setError(undefined)))
        .catch((e: Error) => alive && setError(e.message))
        .finally(() => alive && setLoading(false));
    setLoading(true);
    run();
    const timer = intervalMs ? window.setInterval(run, intervalMs) : undefined;
    const onVisible = () => {
      if (document.visibilityState === "visible") run();
    };
    if (refetchOnFocus) {
      document.addEventListener("visibilitychange", onVisible);
      window.addEventListener("focus", onVisible);
    }
    return () => {
      alive = false;
      if (timer) window.clearInterval(timer);
      if (refetchOnFocus) {
        document.removeEventListener("visibilitychange", onVisible);
        window.removeEventListener("focus", onVisible);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, intervalMs, refetchOnFocus]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

/** `value`, but only after it has stopped changing for `ms` (e.g. while a slider drags). */
export function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return settled;
}
