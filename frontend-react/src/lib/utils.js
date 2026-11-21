export const fileName = (path = "") => path.split("/").pop() || path;

export const dirName = (path = "") => {
  const parts = path.split("/");
  parts.pop();
  return parts.join("/");
};

// Accepts "owner/repo", "github.com/owner/repo" or a full URL and returns a
// full https URL (the backend requires "github.com" in the URL).
export function normalizeGithubUrl(input) {
  let v = input.trim().replace(/\.git$/, "").replace(/\/+$/, "");
  if (!v) return null;
  v = v.replace(/^https?:\/\//, "").replace(/^www\./, "");
  if (v.startsWith("github.com/")) v = v.slice("github.com/".length);
  const parts = v.split("/").filter(Boolean);
  if (parts.length < 2) return null;
  return `https://github.com/${parts[0]}/${parts[1]}`;
}

export function formatNumber(n) {
  if (n == null) return "—";
  return new Intl.NumberFormat().format(n);
}

export function timeAgo(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const s = Math.round((Date.now() - d.getTime()) / 1000);
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return d.toLocaleDateString();
}

export const isTestPath = (p = "") => /(^|\/)(tests?|testing)(\/|$)|(^|\/)test_[^/]*\.py$|_test\.py$|conftest\.py$/.test(p);

// localStorage can be unavailable (private mode) — never let it crash the app.
export const storage = {
  get(key, fallback) {
    try {
      const v = localStorage.getItem(key);
      return v == null ? fallback : JSON.parse(v);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* ignore */
    }
  },
};

// Stable categorical colors for graph groups (package prefixes).
export const GROUP_COLORS = [
  "#34D399", "#A78BFA", "#FBBF24", "#38BDF8",
  "#F472B6", "#2DD4BF", "#A3E635", "#FB923C",
];
export function colorForGroup(group = "") {
  let h = 0;
  for (let i = 0; i < group.length; i++) h = (h * 31 + group.charCodeAt(i)) >>> 0;
  return GROUP_COLORS[h % GROUP_COLORS.length];
}

// Reads a CSS custom property from the active theme (used by the graph canvas,
// which draws on <canvas> and cannot use CSS variables directly).
export const cssVar = (name) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();
