import { useCallback, useEffect, useRef, useState } from "react";

export interface Resource<T> {
  data: T | undefined;
  error: string | undefined;
  loading: boolean;
  reload: () => void;
}

/** Fetch on mount and whenever deps change; optionally re-poll every `intervalMs`. */
export function useResource<T>(
  fetcher: () => Promise<T>,
  deps: unknown[],
  intervalMs?: number,
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
    return () => {
      alive = false;
      if (timer) window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, intervalMs]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}
