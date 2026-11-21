import { useEffect, useState } from "react";
import {
  ChevronDown, ExternalLink, FileText, Loader2, PackageCheck, RefreshCw, ShieldAlert, ShieldCheck,
} from "lucide-react";
import { EmptyState } from "../components/States";

const SEVERITIES = ["Critical", "High", "Medium", "Low", "Unknown"];

function SevBadge({ sev }) {
  const s = SEVERITIES.includes(sev) ? sev : "Unknown";
  return <span className={`sev ${s.toLowerCase()}`}>{s}</span>;
}

function Collapsible({ title, icon: Icon, children }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`collapse ${open ? "open" : ""}`}>
      <button className="collapse-head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {Icon && <Icon size={16} aria-hidden />}
        <span>{title}</span>
        <ChevronDown size={16} className="chev" aria-hidden />
      </button>
      {open && <div className="collapse-body">{children}</div>}
    </div>
  );
}

function VulnTable({ pkgs }) {
  return (
    <div className="table-wrap">
      <table className="vuln-table">
        <thead>
          <tr>
            <th>Package</th><th>Version</th><th>Advisory</th>
            <th>Severity</th><th className="num">Score</th><th>Fixed in</th>
          </tr>
        </thead>
        {pkgs.map((p) => (
          <tbody key={`${p.ecosystem}:${p.name}`}>
            {(p.vulnerabilities || []).map((v, i) => (
              <tr key={v.id}>
                <td>
                  {i === 0 && (
                    <div className="pkg-cell">
                      <div className="mono strong">
                        {p.name} {p.dev && <span className="tag">dev</span>}
                      </div>
                      <div className="muted small">
                        <span className="tag">{p.ecosystem}</span>{" "}
                        {(p.source_files || [])[0]}
                        {(p.source_files || []).length > 1 && ` +${p.source_files.length - 1}`}
                      </div>
                    </div>
                  )}
                </td>
                <td>
                  {i === 0 && (
                    <>
                      <code className="inline-code">{p.version}</code>
                      {!p.exact && (
                        <div className="small amber-text" title={p.note || "Version inferred from a range"}>
                          inferred{p.declared ? ` · ${p.declared}` : ""}
                        </div>
                      )}
                    </>
                  )}
                </td>
                <td className="adv-cell">
                  <a
                    className="mono adv-link"
                    href={`https://osv.dev/vulnerability/${encodeURIComponent(v.id)}`}
                    target="_blank" rel="noopener noreferrer"
                  >
                    {v.id} <ExternalLink size={12} />
                  </a>
                  {v.cve && v.cve !== v.id && <span className="muted mono small"> {v.cve}</span>}
                  {v.summary && <div className="adv-summary">{v.summary}</div>}
                </td>
                <td><SevBadge sev={v.severity} /></td>
                <td className="num mono">{typeof v.score === "number" ? v.score.toFixed(1) : "—"}</td>
                <td>
                  {v.fixed_version
                    ? <code className="inline-code ok">{v.fixed_version}</code>
                    : <span className="muted small">none listed</span>}
                </td>
              </tr>
            ))}
          </tbody>
        ))}
      </table>
    </div>
  );
}

export default function SecurityView({ repo, sec, onScan }) {
  // scan automatically the first time the tab is opened for a repo
  useEffect(() => {
    if (!sec || (!sec.data && !sec.error && !sec.scanning)) onScan(repo.id);
  }, [repo.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const scanning = sec?.scanning;
  const data = sec?.data;
  const summary = data?.summary || {};
  const bySev = summary.by_severity || {};
  const results = Array.isArray(data?.results) ? data.results : [];
  const skipped = Array.isArray(data?.skipped) ? data.skipped : [];
  const manifests = Array.isArray(data?.manifests) ? data.manifests : [];
  const vulnerable = results.filter((r) => r.status === "vulnerable");
  const clean = results.filter((r) => r.status === "clean");
  const unchecked = results.filter((r) => r.status === "unchecked");
  const when = data?.scanned_at ? new Date(data.scanned_at) : null;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Dependency vulnerabilities</h2>
          <p className="muted">
            Checks packages declared in <code className="inline-code">requirements*.txt</code> and{" "}
            <code className="inline-code">package.json</code> against OSV.dev. Only package names and
            versions are sent — never source code.
          </p>
        </div>
        <button className="btn primary" onClick={() => onScan(repo.id)} disabled={scanning}>
          {scanning ? <><Loader2 size={16} className="spin" /> Scanning…</> : <><RefreshCw size={16} /> {data ? "Re-scan" : "Scan dependencies"}</>}
        </button>
      </div>

      {scanning && !data && (
        <div className="stat-row">
          {[0, 1, 2, 3, 4].map((i) => <div key={i} className="skeleton stat-skel" />)}
        </div>
      )}

      {sec?.error && !scanning && (
        <div className="alert danger"><ShieldAlert size={16} /> {sec.error}</div>
      )}

      {data?.status === "no_dependencies" && (
        <EmptyState icon={PackageCheck} title="No dependencies to check">{data.message}</EmptyState>
      )}

      {data && data.status !== "no_dependencies" && (
        <>
          <div className="scan-meta mono small muted">
            {summary.packages_total || 0} packages · {manifests.length} file{manifests.length === 1 ? "" : "s"}
            {Object.entries(data.by_ecosystem || {}).map(([k, g]) => ` · ${k}: ${g.packages}`).join("")}
            {` · source: ${data.source || "OSV.dev"}`}
            {when && !isNaN(when) && ` · ${when.toLocaleString()}`}
          </div>

          <div className="stat-row">
            {["Critical", "High", "Medium", "Low"].map((s) => (
              <div key={s} className={`stat sev-${s.toLowerCase()} ${bySev[s] ? "" : "zero"}`}>
                <div className="stat-num">{bySev[s] || 0}</div>
                <div className="stat-label">{s}</div>
              </div>
            ))}
            {bySev.Unknown > 0 && (
              <div className="stat sev-unknown">
                <div className="stat-num">{bySev.Unknown}</div>
                <div className="stat-label">Unknown</div>
              </div>
            )}
            <div className="stat sev-clean">
              <div className="stat-num">{summary.packages_clean || 0}</div>
              <div className="stat-label">Clean packages</div>
            </div>
          </div>

          <div className="notes">
            {manifests.length > 0 && (
              <div className="note">
                <FileText size={14} aria-hidden /> Scanned:{" "}
                {manifests.map((m) => <code key={m} className="inline-code">{m}</code>)}
              </div>
            )}
            {results.some((r) => r.status !== "unchecked" && !r.exact) && (
              <div className="note">
                Versions marked <span className="amber-text">inferred</span> come from a version range, not an exact pin —
                the installed version may be newer and unaffected.
              </div>
            )}
            {bySev.Unknown > 0 && (
              <div className="note">“Unknown” severity means OSV has no CVSS v3 score or label for that advisory.</div>
            )}
            {data.truncated && <div className="note">Only the first dependency files found were scanned (safety limit).</div>}
          </div>

          {vulnerable.length > 0 ? (
            <VulnTable pkgs={vulnerable} />
          ) : clean.length > 0 ? (
            <div className="alert ok"><ShieldCheck size={16} /> No known vulnerabilities in the dependencies that could be checked.</div>
          ) : null}

          <div className="collapses">
            {clean.length > 0 && (
              <Collapsible icon={PackageCheck} title={`${clean.length} package${clean.length === 1 ? "" : "s"} with no known vulnerabilities`}>
                <div className="chips">
                  {clean.map((p) => (
                    <span key={`${p.ecosystem}:${p.name}`} className="chip static" title={(p.source_files || []).join(", ")}>
                      <span className="mono">{p.name}</span>
                      <span className="muted mono">{p.version}{p.exact ? "" : "*"}</span>
                    </span>
                  ))}
                </div>
              </Collapsible>
            )}
            {unchecked.length > 0 && (
              <Collapsible title={`${unchecked.length} package${unchecked.length === 1 ? "" : "s"} not checked (no usable version)`}>
                {unchecked.map((p) => (
                  <div key={p.name} className="list-row">
                    <span className="mono strong">{p.name}</span>
                    <span className="muted small"> ({p.ecosystem}, declared {p.declared}) — {p.note || "no version to check"}</span>
                  </div>
                ))}
              </Collapsible>
            )}
            {skipped.length > 0 && (
              <Collapsible title={`${skipped.length} entr${skipped.length === 1 ? "y" : "ies"} skipped`}>
                {skipped.map((s, i) => (
                  <div key={i} className="list-row">
                    <code className="inline-code">{s.entry}</code>
                    <span className="muted small"> in {s.file} — {s.reason}</span>
                  </div>
                ))}
              </Collapsible>
            )}
          </div>
        </>
      )}
    </div>
  );
}
