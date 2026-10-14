const API_BASE = "";   // same-origin; empty string works when HTML is served by FastAPI
let currentRepoId = null;
let currentRepoName = null;
let pollTimer = null;
// Tab switching
function switchTab(name) {
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.getElementById('panel-' + name).classList.add('active');
    document.getElementById('tab-' + name).classList.add('active');
    if (name === 'arch') loadGraph();
    if (name === 'sec') loadSecurity();
}

// Show file in right panel
async function showFile(path, line) {
  if (!currentRepoId) return;

  const res = await fetch(
    `${API_BASE}/api/file?repo_id=${currentRepoId}&path=${encodeURIComponent(path)}&start=${line}&end=${line + 30}`
  );
  if (!res.ok) return;

  const data = await res.json();
  document.getElementById("right-title").textContent = "Source · " + path.split("/").pop();
  document.getElementById("preview-path").textContent = path;

  const codeHtml = data.lines.map(l =>
    `<span class="line ${l.n >= line && l.n <= line + 20 ? 'highlight' : ''}">
       <span class="line-num">${l.n}</span>${escHtml(l.text)}
     </span>`
  ).join("");

  document.getElementById("preview-code").innerHTML = codeHtml;
  document.getElementById("right-panel").style.display = "flex";
}

function closeRight() {
// keep panel but could hide on mobile
}

// Toggle security finding
function toggleFinding(el) {
    el.classList.toggle('open');
}

// Fill Q&A input
function fillQ(text) {
    document.getElementById('qa-input').value = text;
    document.getElementById('qa-input').focus();
}

function loadQuery(text) {
    switchTab('qa');
    fillQ(text);
}

// sending a message
async function sendMessage() {
    const input = document.getElementById("qa-input");
    const text = input.value.trim();
    if (!text) return;
    if (!currentRepoId) {
        alert("Please select a repository first.");
        return;
    }

    const messages = document.getElementById("messages");
    const typingMsg = document.getElementById("typing-msg");

    // Render user message
    const userMsg = document.createElement("div");
    userMsg.className = "msg";
    userMsg.innerHTML = `
        <div class="msg-avatar user">You</div>
        <div class="msg-body">
        <div class="msg-role">You</div>
        <div class="msg-text">${escHtml(text)}</div>
        </div>`;
    messages.insertBefore(userMsg, typingMsg);

    typingMsg.style.display = "flex";
    input.value = "";
    messages.scrollTop = messages.scrollHeight;

    try {
        const res = await fetch(`${API_BASE}/api/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo_id: currentRepoId, question: text }),
        });

        typingMsg.style.display = "none";

        if (!res.ok) {
            const err = await res.json();
            renderAiMessage(`Error: ${err.detail || "unknown error"}`, []);
            return;
        }

        const data = await res.json();
        renderAiMessage(data.answer, data.citations);
    } catch (e) {
        typingMsg.style.display = "none";
        renderAiMessage(`Network error: ${e.message}`, []);
    }
}

// Minimal, safe Markdown renderer for LLM answers.
// Supports: fenced code blocks, `inline code`, **bold**, bullet/numbered lists,
// and # headings (shown as bold). All text is HTML-escaped before any tags are
// added, so model output can never inject HTML or scripts.

function renderMarkdown(text) {
    return marked.parse(String(text || ""), {
        breaks: true,
        gfm: true,
        headerIds: false,
        mangle: false,
    });
}

function renderAiMessage(answerText, citations) {
    const messages = document.getElementById("messages");
    const typingMsg = document.getElementById("typing-msg");

    const citationHtml = citations.map(c =>
        `<a class="cite" onclick="showFile('${c.file}', ${c.start_line})">
        📄 ${c.file.split('/').pop()}:${c.start_line}-${c.end_line}
        </a>`
    ).join(" ");

    const aiMsg = document.createElement("div");
    aiMsg.className = "msg";
    aiMsg.innerHTML = `
        <div class="msg-avatar ai">CS</div>
        <div class="msg-body">
        <div class="msg-role">CodeSense · ${citations.length} sources</div>
        <div class="msg-text markdown-body">
            ${renderMarkdown(answerText)}
            <div class="msg-citations">${citationHtml}</div>
        </div>
        </div>`;
    messages.insertBefore(aiMsg, typingMsg);
    messages.scrollTop = messages.scrollHeight;
}

function handleKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
}

function escHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// Simulate re-index
function simulateIndex() {
    const btn = event.target;
    btn.textContent = '⏳ Indexing…';
    btn.disabled = true;
    setTimeout(() => {
        btn.textContent = '✓ Done';
        setTimeout(() => { btn.textContent = '↻ Re-index'; btn.disabled = false; }, 1500);
    }, 2000);
}

// Impact analysis
function runImpact() {
    const val = document.getElementById('impact-input').value.trim();
    if (!val) return;
    const btn = event.target;
    btn.textContent = 'Analysing…';
    btn.disabled = true;
    setTimeout(() => { btn.textContent = 'Analyse'; btn.disabled = false; }, 1200);
}

async function loadRepositories() {
    const res = await fetch(`${API_BASE}/api/repositories`);
    const { data } = await res.json();
    const container = document.querySelector(".sidebar-section");

    // Keep the "Indexed repos" label but replace the cards
    const cardsHtml = data.map(repo => `
        <div class="repo-card ${repo.id === currentRepoId ? 'active' : ''}"
            onclick="selectRepo('${repo.id}', '${repo.name}')">
        <div class="repo-name">${repo.name}</div>
        <div class="repo-meta">
            ${repo.status === 'ready'
            ? `<span class="dot-green"></span> Indexed · ${repo.chunk_count} chunks`
            : repo.status === 'failed'
            ? `<span style="color:var(--red)">✗ Failed</span>`
            : `<span style="color:var(--amber)">⏳ ${repo.stage}… ${repo.progress}%</span>`}
        </div>
        </div>
    `).join("");

    container.innerHTML = `
        <div class="sidebar-label">Indexed repos</div>
        ${cardsHtml || '<div style="font-size:12px;color:var(--muted);padding:0 8px">No repositories yet.</div>'}
    `;
}

function selectRepo(id, name) {
    currentRepoId = id;
    currentRepoName = name;
    onSecurityRepoChanged();
    loadRepositories();
    if (pollTimer) clearInterval(pollTimer);
    startPolling(id);
}

function startPolling(repoId) {
    pollTimer = setInterval(async () => {
        const res = await fetch(`${API_BASE}/api/repositories/${repoId}/status`);
        const data = await res.json();
        loadRepositories();
        if (data.status === "ready" || data.status === "failed") {
            clearInterval(pollTimer);
            pollTimer = null;   
        }
    }, 2000);
}

// Load on page start
loadRepositories();
setInterval(loadRepositories, 5000);

async function connectRepository() {
    const input = document.querySelector(".url-input");
    const url = input.value.trim();
    if (!url) return;

    const btn = event.target;
    btn.textContent = "⏳ Connecting…";
    btn.disabled = true;

    try {
        const res = await fetch(`${API_BASE}/api/repositories`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
        });

        if (!res.ok) {
            const err = await res.json();
            alert(`Failed to connect: ${err.detail || "unknown error"}`);
            return;
        }

        const repo = await res.json();
        currentRepoId = repo.id;
        currentRepoName = repo.name;
        loadRepositories();
        startPolling(repo.id);
    } finally {
        btn.textContent = "↻ Re-index";
        btn.disabled = false;
    }
}
// ===== GRAPH VISUALIZATION =====
let cy = null;
let currentGraphLevel = 'module';

function setGraphLevel(level) {
    currentGraphLevel = level;
    document.querySelectorAll('#arch-level-module, #arch-level-package, #arch-level-function')
        .forEach(b => b.classList.remove('active'));
    const btn = document.getElementById(`arch-level-${level}`);
    if (btn) btn.classList.add('active');
    loadGraph();
}

async function loadGraph() {
    const canvas = document.getElementById('graph-canvas');
    const stats = document.getElementById('arch-stats');
    if (!canvas) return;

    if (!currentRepoId) {
        canvas.innerHTML = '<div style="padding:40px;color:#888;text-align:center">Select a repository from the sidebar first.</div>';
        if (stats) stats.textContent = '';
        return;
    }

    canvas.innerHTML = '<div style="padding:40px;color:#888;text-align:center">Loading graph…</div>';
    if (stats) stats.textContent = '';

    try {
        const res = await fetch(`${API_BASE}/api/graph/${currentRepoId}?level=${currentGraphLevel}`);
        if (!res.ok) {
            canvas.innerHTML = '<div style="padding:40px;color:#f66;text-align:center">Graph not found. Make sure the repo finished indexing.</div>';
            return;
        }
        const data = await res.json();

        if (data.nodes.length === 0) {
            canvas.innerHTML = '<div style="padding:40px;color:#888;text-align:center">Graph is empty for this level.</div>';
            return;
        }

        if (stats) {
            stats.textContent = `${data.nodes.length} nodes · ${data.edges.length} edges`;
        }

        canvas.innerHTML = '';
        if (cy) { try { cy.destroy(); } catch(e){} cy = null; }

        cy = cytoscape({
            container: canvas,
            elements: [
                ...data.nodes.map(n => {
                    const parts = (n.file || n.id).split('/');
                    const group = parts.length > 1 ? parts.slice(0, 2).join('/') : parts[0];
                    return {
                        data: {
                            id: n.id,
                            label: n.label,
                            pr: Math.max(n.pagerank, 0.001),
                            file: n.file,
                            group: group,
                        }
                    };
                }),
                ...data.edges.map(e => ({
                    data: { source: e.source, target: e.target }
                }))
            ],
            layout: {
                name: 'fcose',
                animate: false,
                quality: 'default',
                nodeRepulsion: 8000,
                idealEdgeLength: 90,
                gravity: 0.3,
                gravityRange: 3.8,
                padding: 30,
                randomize: true,
                nodeSeparation: 100,
            },
            style: [
                {
                    selector: 'node',
                    style: {
                        'background-color': function(ele) {
                            const g = ele.data('group') || 'x';
                            let hash = 0;
                            for (let i = 0; i < g.length; i++) hash = (hash * 31 + g.charCodeAt(i)) % 360;
                            return `hsl(${hash}, 45%, 55%)`;
                        },
                        'border-color': '#0d1115',
                        'border-width': 1,
                        'label': 'data(label)',
                        'color': '#e6e8ec',
                        'font-size': '10px',
                        'text-valign': 'center',
                        'text-halign': 'center',
                        'text-outline-color': '#0d1115',
                        'text-outline-width': 2,
                        'width': 'mapData(pr, 0, 0.08, 24, 70)',
                        'height': 'mapData(pr, 0, 0.08, 24, 70)',
                        'text-opacity': 'mapData(pr, 0, 0.02, 0, 1)',
                    }
                },
                {
                    selector: 'edge',
                    style: {
                        'width': 1,
                        'line-color': '#2e3545',
                        'target-arrow-color': '#2e3545',
                        'target-arrow-shape': 'triangle',
                        'curve-style': 'bezier',
                        'opacity': 0.5,
                        'arrow-scale': 0.8,
                    }
                },
                {
                    selector: '.faded',
                    style: {
                        'opacity': 0.12,
                    }
                },
                {
                    selector: 'node.hover',
                    style: {
                        'text-opacity': 1,
                        'border-color': '#4f8ef7',
                        'border-width': 2,
                        'z-index': 999,
                        'opacity': 1,
                    }
                },
                {
                    selector: 'edge.hover',
                    style: {
                        'line-color': '#4f8ef7',
                        'target-arrow-color': '#4f8ef7',
                        'opacity': 1,
                        'width': 2,
                    }
                },
                {
                    selector: 'node.pinned',
                    style: {
                        'text-opacity': 1,
                        'border-color': '#B45309',
                        'border-width': 3,
                        'z-index': 999,
                        'opacity': 1,
                    }
                },
                {
                    selector: 'edge.pinned',
                    style: {
                        'line-color': '#B45309',
                        'target-arrow-color': '#B45309',
                        'opacity': 1,
                        'width': 2,
                    }
                }
            ]
        });

        // Hover: temporary highlight
        cy.on('mouseover', 'node', (evt) => {
            if (cy.$('.pinned').length > 0) return;   // don't fight the pin
            const node = evt.target;
            const connected = node.closedNeighborhood();
            cy.elements().addClass('faded');
            connected.removeClass('faded').addClass('hover');
            connected.connectedEdges().removeClass('faded').addClass('hover');
        });
        cy.on('mouseout', 'node', () => {
            if (cy.$('.pinned').length > 0) return;
            cy.elements().removeClass('faded hover');
        });

        // Click a node: pin highlight + open file
        cy.on('tap', 'node', (evt) => {
            const node = evt.target;
            const connected = node.closedNeighborhood();
            cy.elements().removeClass('pinned hover faded');
            cy.elements().addClass('faded');
            connected.removeClass('faded').addClass('pinned');
            connected.connectedEdges().removeClass('faded').addClass('pinned');
            const file = node.data('file');
            if (file && typeof showFile === 'function') {
                showFile(file, 1);
            }
            evt.stopPropagation();
        });

        // Click empty space: clear pin
        cy.on('tap', (evt) => {
            if (evt.target === cy) {
                cy.elements().removeClass('pinned hover faded');
            }
        });

        // Fit to viewport
        cy.fit(cy.elements(), 40);

    } catch (e) {
        canvas.innerHTML = `<div style="padding:40px;color:#f66;text-align:center">Error: ${e.message}</div>`;
    }
}

// ── Security tab: dependency vulnerability scan ──────────────────────────
// Calls POST /api/security/analyze. Every server-provided string is escaped
// before it is put into the page.
let secLoadedFor = null;   // repo id the current results belong to
let secScanning = false;

function secEsc(v) {
    return String(v ?? '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function secPanelActive() {
    const p = document.getElementById('panel-sec');
    return !!p && p.classList.contains('active');
}

function secSetTabCount(n) {
    const el = document.getElementById('sec-tab-count');
    if (!el) return;
    if (n > 0) { el.textContent = n; el.style.display = ''; }
    else { el.textContent = ''; el.style.display = 'none'; }
}

function secClear() {
    document.getElementById('sec-status').innerHTML = '';
    document.getElementById('sec-summary').innerHTML = '';
    document.getElementById('sec-notices').innerHTML = '';
    secSetTabCount(0);
}

function secShow(html) {
    document.getElementById('sec-results').innerHTML = html;
}

// Called from selectRepo(): drop results that belong to another repository.
function onSecurityRepoChanged() {
    if (secLoadedFor === currentRepoId) return;
    secClear();
    secLoadedFor = null;
    secShow('<div class="sec-empty">Open this tab to scan the selected repository.</div>');
    if (secPanelActive()) loadSecurity();
}

// Called from switchTab('sec'): scan once per repository, then reuse results.
function loadSecurity() {
    if (!currentRepoId) {
        secClear();
        secShow('<div class="sec-empty">Select a repository from the sidebar first.</div>');
        return;
    }
    if (secLoadedFor !== currentRepoId && !secScanning) runSecurityScan();
}

async function runSecurityScan() {
    if (secScanning) return;
    if (!currentRepoId) { loadSecurity(); return; }

    const repoId = currentRepoId;
    const btn = document.getElementById('sec-scan-btn');
    secScanning = true;
    btn.disabled = true;
    btn.textContent = 'Scanning…';
    secClear();
    secShow('<div class="sec-empty">Scanning dependencies…</div>');

    let data = null;
    let errorMsg = null;
    try {
        const res = await fetch(`${API_BASE}/api/security/analyze`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ repo_id: repoId }),
        });
        let body = null;
        try { body = await res.json(); } catch (_) { /* not JSON */ }
        if (res.ok) {
            data = body;
        } else if (body && typeof body.detail === 'string') {
            errorMsg = body.detail;
        } else {
            errorMsg = `The scan failed (HTTP ${res.status}).`;
        }
    } catch (e) {
        errorMsg = 'Could not reach the CodeSense server.';
    }

    secScanning = false;
    btn.disabled = false;
    btn.textContent = 'Re-scan';

    if (repoId !== currentRepoId) {          // user switched repository mid-scan
        if (secPanelActive()) loadSecurity();
        return;
    }
    if (errorMsg || !data) {
        secShow(`<div class="sec-error">${secEsc(errorMsg || 'The scan returned no data.')}</div>`);
        return;
    }
    secLoadedFor = repoId;
    renderSecurity(data);
}

function secSeverityBadge(sev) {
    const known = ['Critical', 'High', 'Medium', 'Low'];
    const s = known.includes(sev) ? sev : 'Unknown';
    return `<span class="sev-badge ${s.toLowerCase()}">${s}</span>`;
}

function secPackageCell(p) {
    const files = p.source_files || [];
    const where = files.length
        ? secEsc(files[0]) + (files.length > 1 ? ` +${files.length - 1} more` : '')
        : '';
    return `<div class="sec-pkg-name" title="${secEsc(files.join(', '))}">${secEsc(p.name)}` +
        (p.dev ? ' <span class="sec-tag">dev</span>' : '') + `</div>` +
        (where ? `<div class="sec-muted">${where}</div>` : '');
}

function secVersionCell(p) {
    let h = `<code>${secEsc(p.version)}</code>`;
    if (!p.exact) {
        h += ` <span class="sec-inferred" title="${secEsc(p.note || 'Version inferred from a range')}">inferred</span>`;
        if (p.declared) h += `<div class="sec-muted">declared ${secEsc(p.declared)}</div>`;
    }
    return h;
}

function secVulnCell(v) {
    const link = `https://osv.dev/vulnerability/${encodeURIComponent(v.id)}`;
    let h = `<a class="sec-vuln-id" href="${secEsc(link)}" target="_blank" rel="noopener noreferrer">${secEsc(v.id)}</a>`;
    if (v.cve && v.cve !== v.id) h += ` <span class="sec-cve">${secEsc(v.cve)}</span>`;
    if (v.summary) h += `<div class="sec-vuln-text">${secEsc(v.summary)}</div>`;
    return h;
}

function secRenderTable(pkgs) {
    const head = '<thead><tr><th>Ecosystem</th><th>Package</th><th>Version</th>' +
        '<th>Vulnerability</th><th>Severity</th><th>Score</th><th>Fixed in</th></tr></thead>';
    const body = pkgs.map(p => {
        const rows = (p.vulnerabilities || []).map((v, i) => {
            const first = i === 0;
            const score = typeof v.score === 'number' ? v.score.toFixed(1) : '—';
            const fix = v.fixed_version
                ? `<code>${secEsc(v.fixed_version)}</code>`
                : '<span class="sec-muted">none listed</span>';
            return `<tr>
                <td>${first ? `<span class="sec-tag">${secEsc(p.ecosystem)}</span>` : ''}</td>
                <td>${first ? secPackageCell(p) : ''}</td>
                <td>${first ? secVersionCell(p) : ''}</td>
                <td>${secVulnCell(v)}</td>
                <td>${secSeverityBadge(v.severity)}</td>
                <td class="sec-score">${score}</td>
                <td>${fix}</td>
            </tr>`;
        }).join('');
        return `<tbody class="sec-pkg">${rows}</tbody>`;
    }).join('');
    return `<div class="sec-table-wrap"><table class="sec-table">${head}${body}</table></div>`;
}

function secDetails(title, inner) {
    return `<details class="sec-more"><summary>${title}</summary><div class="sec-more-body">${inner}</div></details>`;
}

function secRenderLists(results, skipped) {
    let html = '';
    const clean = results.filter(r => r.status === 'clean');
    const unchecked = results.filter(r => r.status === 'unchecked');

    if (clean.length) {
        const chips = clean.map(p =>
            `<span class="sec-chip" title="${secEsc((p.source_files || []).join(', '))}">${secEsc(p.name)}` +
            `<em>${secEsc(p.version)}${p.exact ? '' : ' (inferred)'}</em></span>`).join('');
        html += secDetails(`${clean.length} package${clean.length === 1 ? '' : 's'} with no known vulnerabilities`,
            `<div class="sec-chips">${chips}</div>`);
    }
    if (unchecked.length) {
        const rows = unchecked.map(p =>
            `<div class="sec-list-row"><strong>${secEsc(p.name)}</strong> ` +
            `<span class="sec-muted">(${secEsc(p.ecosystem)}, declared ${secEsc(p.declared)})</span> — ` +
            `${secEsc(p.note || 'no version to check')}</div>`).join('');
        html += secDetails(`${unchecked.length} package${unchecked.length === 1 ? '' : 's'} not checked (no usable version)`, rows);
    }
    if (skipped.length) {
        const rows = skipped.map(s =>
            `<div class="sec-list-row"><code>${secEsc(s.entry)}</code> ` +
            `<span class="sec-muted">in ${secEsc(s.file)}</span> — ${secEsc(s.reason)}</div>`).join('');
        html += secDetails(`${skipped.length} entr${skipped.length === 1 ? 'y' : 'ies'} skipped`, rows);
    }
    return html;
}

function renderSecurity(data) {
    const summary = data.summary || {};
    const sev = summary.by_severity || {};
    const results = Array.isArray(data.results) ? data.results : [];
    const skipped = Array.isArray(data.skipped) ? data.skipped : [];
    const manifests = Array.isArray(data.manifests) ? data.manifests : [];

    if (data.status === 'no_dependencies') {
        secShow(`<div class="sec-empty">${secEsc(data.message || 'No dependencies found.')}</div>` +
            secRenderLists([], skipped));
        return;
    }

    // status line
    const d = new Date(data.scanned_at);
    const when = isNaN(d) ? '' : d.toLocaleString();
    const eco = Object.entries(data.by_ecosystem || {})
        .map(([name, g]) => `${secEsc(name)}: ${secEsc(g.packages)}`).join(' · ');
    document.getElementById('sec-status').innerHTML =
        `${secEsc(summary.packages_total || 0)} packages · ${manifests.length} file${manifests.length === 1 ? '' : 's'}` +
        (eco ? ` · ${eco}` : '') +
        ` · source: ${secEsc(data.source || 'OSV.dev')}` + (when ? ` · ${secEsc(when)}` : '');

    // severity cards
    const cards = [
        ['Critical', sev.Critical, 's-critical'],
        ['High', sev.High, 's-high'],
        ['Medium', sev.Medium, 's-medium'],
        ['Low', sev.Low, 's-low'],
    ];
    if (sev.Unknown > 0) cards.push(['Unknown', sev.Unknown, 's-unknown']);
    cards.push(['Clean packages', summary.packages_clean, 'passed']);
    document.getElementById('sec-summary').innerHTML = cards.map(([label, n, cls]) =>
        `<div class="sec-stat ${cls}"><div class="sec-stat-num">${secEsc(n || 0)}</div>` +
        `<div class="sec-stat-label">${secEsc(label)}</div></div>`).join('');

    // notices
    const notices = [];
    if (manifests.length) {
        notices.push('Files scanned: ' + manifests.map(m => `<code>${secEsc(m)}</code>`).join(' '));
    }
    if (results.some(r => r.status !== 'unchecked' && !r.exact)) {
        notices.push('Versions marked <em>inferred</em> come from a version range, not an exact pin. ' +
            'The version actually installed may be newer and not affected.');
    }
    if (sev.Unknown > 0) {
        notices.push('<em>Unknown</em> severity means OSV has no CVSS v3 score or severity label for that advisory.');
    }
    if (data.truncated) {
        notices.push('Only the first dependency files found were scanned (safety limit).');
    }
    document.getElementById('sec-notices').innerHTML =
        notices.map(n => `<div class="sec-note">${n}</div>`).join('');

    // results
    const vulnerable = results.filter(r => r.status === 'vulnerable');
    let html = '';
    if (vulnerable.length) {
        html += secRenderTable(vulnerable);
    } else if (results.some(r => r.status === 'clean')) {
        html += '<div class="sec-clean-banner">No known vulnerabilities found in the dependencies that could be checked.</div>';
    }
    html += secRenderLists(results, skipped);
    secShow(html);
    secSetTabCount(summary.vulnerabilities_total || 0);
}