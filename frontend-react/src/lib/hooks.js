import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { storage } from "./utils";

// Loads the repository list and keeps it fresh. Polls quickly while any
// repository is still indexing, slowly otherwise.
export function useRepositories() {
  const [repos, setRepos] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(null);
  const timer = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.listRepositories();
      setRepos(data);
      setError(null);
      return data;
    } catch (e) {
      setError(e.message);
      return null;
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      const data = await refresh();
      if (cancelled) return;
      const busy = (data || []).some((r) => r.status === "pending" || r.status === "indexing");
      timer.current = setTimeout(tick, busy ? 2000 : 10000);
    };
    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer.current);
    };
  }, [refresh]);

  return { repos, loaded, error, refresh };
}

// useState that is mirrored to localStorage.
export function usePersistentState(key, initial) {
  const [value, setValue] = useState(() => storage.get(key, initial));
  useEffect(() => storage.set(key, value), [key, value]);
  return [value, setValue];
}

export function useMediaQuery(query) {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setMatches(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return matches;
}
