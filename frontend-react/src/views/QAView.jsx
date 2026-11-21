import { useEffect, useRef, useState } from "react";
import {
  ArrowUp, Check, Copy, FileCode2, Info, RotateCcw, Sparkles, Trash2, TriangleAlert, User,
} from "lucide-react";
import { api } from "../lib/api";
import { fileName } from "../lib/utils";
import Markdown from "../components/Markdown";

const SUGGESTIONS = [
  { q: "Give me a high-level overview of this codebase", hint: "Orientation" },
  { q: "Where is the main entry point and what happens at startup?", hint: "Control flow" },
  { q: "How is configuration loaded and where are settings defined?", hint: "Config" },
  { q: "How are errors and exceptions handled?", hint: "Reliability" },
];

let idSeq = Date.now();
const newId = () => String(idSeq++);

function Citations({ citations, onCite }) {
  if (!citations?.length) return null;
  const max = Math.max(...citations.map((c) => c.score || 0), 0.0001);
  return (
    <div className="sources">
      <div className="sources-label">Sources · {citations.length}</div>
      <div className="source-grid">
        {citations.map((c, i) => (
          <button
            key={`${c.file}:${c.start_line}:${i}`}
            className="source-card"
            onClick={() =>
              onCite({ path: c.file, line: c.start_line, endLine: c.end_line, name: c.name })
            }
            title={`${c.file}:${c.start_line}-${c.end_line}`}
          >
            <span className="source-idx mono">{i + 1}</span>
            <span className="source-card-body">
              <span className="source-card-file mono">
                <FileCode2 size={13} aria-hidden />
                <span className="ellipsis">
                  {fileName(c.file)}<span className="muted">:{c.start_line}–{c.end_line}</span>
                </span>
              </span>
              {c.name && <span className="source-card-name mono">{c.name}</span>}
            </span>
            {typeof c.score === "number" && (
              <span className="relevance" title={`Similarity ${c.score.toFixed(3)}`}>
                <span style={{ width: `${Math.max(12, (c.score / max) * 100)}%` }} />
              </span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}

function AiMessage({ msg, onCite }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(msg.text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch { /* ignore */ }
  };
  return (
    <div className="msg ai">
      <div className="avatar ai" aria-hidden><Sparkles size={15} /></div>
      <div className="msg-body">
        <div className="msg-head">
          <span className="msg-author">CodeSense</span>
          {msg.answerSource === "fallback" && (
            <span className="tag amber" title="The LLM was unavailable (missing GROQ_API_KEY or API error), so a template answer was returned.">
              <Info size={11} /> Template answer
            </span>
          )}
          <button className="icon-btn sm ghost-hover" onClick={copy} aria-label="Copy answer">
            {copied ? <Check size={13} /> : <Copy size={13} />}
          </button>
        </div>
        <Markdown>{msg.text}</Markdown>
        <Citations citations={msg.citations} onCite={onCite} />
      </div>
    </div>
  );
}

export default function QAView({ repo, messages, setMessages, onCite, prefill, onAsked }) {
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const scrollRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    if (prefill?.text) {
      setDraft(prefill.text);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [prefill]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages.length, pending]);

  // auto-grow the textarea
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 180) + "px";
  }, [draft]);

  const ask = async (question) => {
    const q = (question ?? draft).trim();
    if (!q || pending) return;
    setDraft("");
    onAsked(q);
    const repoId = repo.id;
    setMessages(repoId, (m) => [...m, { id: newId(), role: "user", text: q }]);
    setPending(true);
    try {
      const data = await api.query(repoId, q);
      setMessages(repoId, (m) => [
        ...m,
        {
          id: newId(),
          role: "ai",
          text: data.answer,
          citations: data.citations || [],
          answerSource: data.answer_source,
        },
      ]);
    } catch (e) {
      setMessages(repoId, (m) => [...m, { id: newId(), role: "error", text: e.message, question: q }]);
    } finally {
      setPending(false);
    }
  };

  const retry = (msg) => {
    // drop the failed exchange (the error and the question right before it), then ask again
    setMessages(repo.id, (m) => {
      const i = m.findIndex((x) => x.id === msg.id);
      if (i < 0) return m;
      const start = i > 0 && m[i - 1].role === "user" ? i - 1 : i;
      return [...m.slice(0, start), ...m.slice(i + 1)];
    });
    ask(msg.question);
  };

  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      ask();
    }
  };

  const empty = messages.length === 0;

  return (
    <div className="qa">
      <div className="qa-scroll" ref={scrollRef}>
        {empty ? (
          <div className="qa-hero">
            <div className="hero-icon"><Sparkles size={22} /></div>
            <h2>What do you want to understand in <span className="accent">{repo.name}</span>?</h2>
            <p className="muted">
              Ask in plain English. Every answer cites the exact files and lines it was drawn from —
              click a source to read the code.
            </p>
            <div className="suggest-grid">
              {SUGGESTIONS.map((s) => (
                <button key={s.q} className="suggest-card" onClick={() => ask(s.q)}>
                  <span className="suggest-hint mono">{s.hint}</span>
                  <span>{s.q}</span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="thread">
            <div className="thread-toolbar">
              <span className="muted small">{messages.filter((m) => m.role === "user").length} questions in this conversation</span>
              <button className="btn ghost sm" onClick={() => setMessages(repo.id, () => [])}>
                <Trash2 size={14} /> Clear chat
              </button>
            </div>
            {messages.map((m) =>
              m.role === "user" ? (
                <div key={m.id} className="msg user">
                  <div className="bubble">{m.text}</div>
                  <div className="avatar user" aria-hidden><User size={15} /></div>
                </div>
              ) : m.role === "ai" ? (
                <AiMessage key={m.id} msg={m} onCite={onCite} />
              ) : (
                <div key={m.id} className="msg ai">
                  <div className="avatar err" aria-hidden><TriangleAlert size={15} /></div>
                  <div className="msg-body">
                    <div className="alert danger">
                      <span>{m.text}</span>
                      <button className="btn sm" onClick={() => retry(m)}>
                        <RotateCcw size={13} /> Retry
                      </button>
                    </div>
                  </div>
                </div>
              )
            )}
            {pending && (
              <div className="msg ai" aria-live="polite">
                <div className="avatar ai" aria-hidden><Sparkles size={15} /></div>
                <div className="msg-body">
                  <div className="thinking">
                    <span className="dots"><i /><i /><i /></span>
                    Searching the index and reading code…
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="composer-wrap">
        <form
          className="composer"
          onSubmit={(e) => { e.preventDefault(); ask(); }}
        >
          <textarea
            ref={inputRef}
            rows={1}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={`Ask anything about ${repo.name}…`}
            aria-label="Your question"
          />
          <button className="send" type="submit" disabled={!draft.trim() || pending} aria-label="Send question">
            <ArrowUp size={18} />
          </button>
        </form>
        <div className="composer-hint muted">
          <kbd>Enter</kbd> to send · <kbd>Shift</kbd>+<kbd>Enter</kbd> for a new line
        </div>
      </div>
    </div>
  );
}
