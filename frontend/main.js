// Navigation Elements
const navNewAudit = document.getElementById('nav-new-audit');
const navHistory = document.getElementById('nav-history');

// Views
const viewNewAudit = document.getElementById('view-new-audit');
const viewHistory = document.getElementById('view-history');
const viewReport = document.getElementById('view-report');

// Navigation Logic
function switchView(targetView) {
  [viewNewAudit, viewHistory, viewReport].forEach(v => v.classList.add('hidden'));
  targetView.classList.remove('hidden');
  
  if (targetView === viewNewAudit) {
    navNewAudit.classList.add('active');
    navHistory.classList.remove('active');
  } else if (targetView === viewHistory) {
    navHistory.classList.add('active');
    navNewAudit.classList.remove('active');
    loadRuns();
  }
}

navNewAudit.addEventListener('click', () => switchView(viewNewAudit));
navHistory.addEventListener('click', () => switchView(viewHistory));

// Run Audit Form Submission
const auditForm = document.getElementById('audit-form');
const auditFeedback = document.getElementById('audit-feedback');
const runAuditBtn = document.getElementById('run-audit-btn');

auditForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  
  const url = document.getElementById('url-input').value;
  const pdf = document.getElementById('pdf-export').checked;
  const diffSince = document.getElementById('diff-since').value;
  
  runAuditBtn.disabled = true;
  runAuditBtn.innerHTML = "Initializing...";
  auditFeedback.classList.add('hidden');
  auditFeedback.className = "feedback-box"; // reset classes
  
  try {
    const res = await fetch('/api/audit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ 
        url, 
        pdf, 
        diff_since: diffSince ? diffSince : null 
      })
    });
    
    if (!res.ok) throw new Error("Failed to start audit");
    
    auditFeedback.classList.remove('hidden');
    auditFeedback.innerHTML = `Success! Agents have been deployed to audit <strong>${url}</strong> in the background. Check the Audit History tab in a few minutes.`;
  } catch (err) {
    auditFeedback.classList.remove('hidden');
    auditFeedback.classList.add('error');
    auditFeedback.innerHTML = `System Error: ${err.message}`;
  } finally {
    runAuditBtn.disabled = false;
    runAuditBtn.innerHTML = `<span class="btn-text">Execute Scan</span>`;
  }
});

// Load Runs (History)
const runsGrid = document.getElementById('runs-grid');
const refreshHistoryBtn = document.getElementById('refresh-history-btn');

async function loadRuns() {
  runsGrid.innerHTML = '<div class="loader">Loading Archives...</div>';
  try {
    const res = await fetch('/api/runs');
    const data = await res.json();
    
    if (!data.runs || data.runs.length === 0) {
      runsGrid.innerHTML = '<p>No audits found in the archive.</p>';
      return;
    }
    
    runsGrid.innerHTML = '';
    data.runs.forEach(run => {
      const card = document.createElement('div');
      card.className = 'run-card glass-panel';
      card.innerHTML = `
        <h3 class="run-host">${run.host}</h3>
        <div class="run-meta">
          <p>ID: ${run.run_id}</p>
          <p>Date: ${formatDate(run.created_at)}</p>
          ${run.has_diff ? '<p style="color:var(--cyan);margin-top:0.5rem">Diff Available</p>' : ''}
        </div>
      `;
      card.addEventListener('click', () => openReport(run.run_id));
      runsGrid.appendChild(card);
    });
  } catch (err) {
    runsGrid.innerHTML = `<p style="color:#ff3b30">Error loading runs: ${err.message}</p>`;
  }
}

refreshHistoryBtn.addEventListener('click', loadRuns);

function formatDate(ts) {
  // Simple format for YYYYMMDDTHHMMSSZ
  if (!ts) return "Unknown";
  try {
    const y = ts.substring(0,4);
    const m = ts.substring(4,6);
    const d = ts.substring(6,8);
    const h = ts.substring(9,11);
    const min = ts.substring(11,13);
    return `${y}-${m}-${d} ${h}:${min}`;
  } catch(e) { return ts; }
}

// Open Report Detail
const reportContainer = document.getElementById('report-container');
const backToHistory = document.getElementById('back-to-history');
const tabBtns = document.querySelectorAll('.tab-btn');

let currentReportData = {};

backToHistory.addEventListener('click', () => {
  switchView(viewHistory);
});

tabBtns.forEach(btn => {
  btn.addEventListener('click', (e) => {
    tabBtns.forEach(b => b.classList.remove('active'));
    e.target.classList.add('active');
    renderTab(e.target.dataset.tab);
  });
});

function renderTab(tabKey) {
  if (!currentReportData) return;
  
  const mapping = {
    'technical': currentReportData.report_md,
    'executive': currentReportData.executive_summary_md,
    'proposal': currentReportData.proposal_md,
    'roadmap': currentReportData.roadmap_md,
    'email': currentReportData.cold_email_txt
  };
  
  const content = mapping[tabKey];
  if (!content) {
    reportContainer.innerHTML = '<p>No data available for this document.</p>';
    return;
  }
  
  // Render text for email, markdown for others
  if (tabKey === 'email') {
    reportContainer.innerHTML = `<pre style="white-space: pre-wrap; font-family: var(--font-body); color: var(--text-bright);">${content}</pre>`;
  } else {
    reportContainer.innerHTML = marked.parse(content);
  }
}

async function openReport(runId) {
  switchView(viewReport);
  document.getElementById('report-title').innerText = runId;
  reportContainer.innerHTML = '<div class="loader">Decrypting Data...</div>';
  
  // Reset to first tab
  tabBtns.forEach(b => b.classList.remove('active'));
  tabBtns[0].classList.add('active');
  
  try {
    const res = await fetch(`/api/runs/${runId}`);
    if (!res.ok) throw new Error("Could not fetch report details");
    
    currentReportData = await res.json();
    renderTab('technical');
    
  } catch (err) {
    reportContainer.innerHTML = `<p style="color:#ff3b30">Error loading report: ${err.message}</p>`;
  }
}
