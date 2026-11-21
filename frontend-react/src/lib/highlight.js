import hljs from "highlight.js/lib/core";
import python from "highlight.js/lib/languages/python";
import javascript from "highlight.js/lib/languages/javascript";
import typescript from "highlight.js/lib/languages/typescript";
import json from "highlight.js/lib/languages/json";
import bash from "highlight.js/lib/languages/bash";
import sql from "highlight.js/lib/languages/sql";
import yaml from "highlight.js/lib/languages/yaml";
import ini from "highlight.js/lib/languages/ini";

hljs.registerLanguage("python", python);
hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("typescript", typescript);
hljs.registerLanguage("json", json);
hljs.registerLanguage("bash", bash);
hljs.registerLanguage("sql", sql);
hljs.registerLanguage("yaml", yaml);
hljs.registerLanguage("ini", ini);

const EXT = {
  py: "python", js: "javascript", jsx: "javascript", mjs: "javascript",
  ts: "typescript", tsx: "typescript", json: "json", sh: "bash",
  sql: "sql", yml: "yaml", yaml: "yaml", toml: "ini", cfg: "ini", ini: "ini",
};

export function languageForPath(path = "") {
  const ext = path.split(".").pop().toLowerCase();
  return EXT[ext] || null;
}

const escapeHtml = (s) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

// Returns safe HTML (highlight.js escapes its input).
export function highlight(code, lang) {
  const language = lang && hljs.getLanguage(lang) ? lang : null;
  try {
    return language
      ? hljs.highlight(code, { language, ignoreIllegals: true }).value
      : escapeHtml(code);
  } catch {
    return escapeHtml(code);
  }
}

// Highlights a list of lines one by one, keeping per-line HTML so line
// numbers and line highlighting stay aligned. Multi-line strings lose
// their colour across lines, which is acceptable for a preview.
export function highlightLines(lines, lang) {
  return lines.map((l) => highlight(l, lang) || "&nbsp;");
}
