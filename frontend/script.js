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

  // NEW: find mermaid code blocks and render them
  renderMermaidIn(aiMsg);

  messages.scrollTop = messages.scrollHeight;
}

/**
 * Auto-fix common Mermaid syntax issues before rendering.
 * - Normalizes Unicode arrows and smart punctuation to ASCII.
 * - Auto-quotes simple [label] definitions whose label contains special chars
 *   AND is not already quoted.
 * Deliberately conservative: labels that already contain " or [ ] are left alone
 * to avoid double-wrapping or corrupting valid syntax.
 */
function sanitizeMermaid(source) {
  // 1. Normalize fancy Unicode characters that Mermaid can't parse
  source = source
    .replace(/[\u2192\u2794\u279C\u27A1\u27F6\u21D2\u21E8\u279D\u27A4]/g, '-->')   // → ➔ ➜ ➡ ⟶ ⇒ ⇨
    .replace(/[\u2190\u2B05\u27F5\u21D0]/g, '<--')                                  // ← ⬅ ⟵ ⇐
    .replace(/[\u2194\u27F7\u21D4]/g, '<-->')                                       // ↔ ⟷ ⇔
    .replace(/[\u2013\u2014]/g, '-')                                                // – —
    .replace(/[\u201C\u201D]/g, '"')                                                // " "
    .replace(/[\u2018\u2019]/g, "'");                                               // ' '

  // 2. Auto-quote simple ID[label] definitions where the label has special chars.
  //    IMPORTANT: the character class [^"\[\]\n] excludes labels that already
  //    contain quotes or nested brackets — those are left ALONE to avoid
  //    corrupting valid syntax.
  return source.replace(
    /([A-Za-z_]\w*)\[([^"\[\]\n]*)\]/g,
    (match, id, label) => {
      if (/^[A-Za-z0-9_ \-]*$/.test(label)) return match;   // already safe
      return `${id}["${label}"]`;
    }
  );
}

async function renderMermaidIn(root) {
  const blocks = root.querySelectorAll('pre > code.language-mermaid');
  for (let i = 0; i < blocks.length; i++) {
    const block = blocks[i];
    const source = sanitizeMermaid(block.textContent);   // Layer 2 runs here
    const id = `mmd-${Date.now()}-${i}`;
    try {
      const { svg } = await mermaid.render(id, source);
      const container = document.createElement('div');
      container.className = 'mermaid-rendered';
      container.innerHTML = svg;
      block.parentElement.replaceWith(container);
    } catch (e) {
      // Layer 3 — friendly fallback instead of the bomb icon
      const fallback = document.createElement('div');
      fallback.className = 'mermaid-fallback';
      fallback.innerHTML = `
        <div class="mermaid-fallback-head">
          ⚠ Diagram couldn't render — the model produced invalid Mermaid syntax.
        </div>
        <details>
          <summary>Show diagram source</summary>
          <pre class="mermaid-fallback-src">${escHtml(source)}</pre>
        </details>
      `;
      block.parentElement.replaceWith(fallback);
      // Mermaid may have injected its own error SVG — clean it up
      const stale = document.getElementById(id);
      if (stale) stale.remove();
      const staleErr = document.querySelector(`[id^="d${id}"]`);
      if (staleErr) staleErr.remove();
    }
  }
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