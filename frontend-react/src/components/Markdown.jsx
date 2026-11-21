import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Copy } from "lucide-react";
import { highlight } from "../lib/highlight";

function CodeBlock({ lang, code }) {
  const [copied, setCopied] = useState(false);
  const html = useMemo(() => highlight(code, lang || "python"), [code, lang]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked */
    }
  };

  return (
    <div className="md-code">
      <div className="md-code-head">
        <span className="mono">{lang || "code"}</span>
        <button className="icon-btn sm" onClick={copy} aria-label="Copy code">
          {copied ? <Check size={14} /> : <Copy size={14} />}
          <span>{copied ? "Copied" : "Copy"}</span>
        </button>
      </div>
      <pre className="hljs">
        <code dangerouslySetInnerHTML={{ __html: html }} />
      </pre>
    </div>
  );
}

// Renders LLM answers. react-markdown never renders raw HTML from the model,
// so answers cannot inject markup or scripts into the page.
export default function Markdown({ children }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          pre: ({ children }) => <>{children}</>,
          code({ className, children }) {
            const text = String(children ?? "");
            const match = /language-(\w+)/.exec(className || "");
            const isBlock = match || text.includes("\n");
            if (!isBlock) return <code className="inline-code">{children}</code>;
            return <CodeBlock lang={match?.[1]} code={text.replace(/\n$/, "")} />;
          },
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {String(children || "")}
      </ReactMarkdown>
    </div>
  );
}
