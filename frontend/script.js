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
        <div class="msg-text">
            <p>${escHtml(answerText)}</p>
            <p>${citationHtml}</p>
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
                ...data.nodes.map(n => ({
                    data: {
                        id: n.id,
                        label: n.label,
                        pr: Math.max(n.pagerank, 0.001),
                        file: n.file,
                    }
                })),
                ...data.edges.map(e => ({
                    data: { source: e.source, target: e.target }
                }))
            ],
            layout: {
                name: 'cose',
                animate: false,
                nodeRepulsion: 10000,
                idealEdgeLength: 90,
                padding: 30,
                randomize: true,
            },
            style: [
                {
                    selector: 'node',
                    style: {
                        'background-color': '#1a1e26',
                        'border-color': '#2e3545',
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
                    selector: 'node:selected',
                    style: {
                        'background-color': '#4f8ef7',
                        'border-color': '#7eb8f7',
                        'border-width': 2,
                    }
                }
            ]
        });

        cy.on('tap', 'node', (evt) => {
            const file = evt.target.data('file');
            if (file && typeof showFile === 'function') {
                showFile(file, 1);
            }
        });

    } catch (e) {
        canvas.innerHTML = `<div style="padding:40px;color:#f66;text-align:center">Error: ${e.message}</div>`;
    }
}