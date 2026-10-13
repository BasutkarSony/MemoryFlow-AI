(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = { theme: 'dark', busy: false };
  const text = (el, value) => { if (el) el.textContent = value; };
  const status = (id, message, kind = '') => { const el = $(id); if (!el) return; el.textContent = message; el.className = 'inline-status' + (kind ? ' ' + kind : ''); };
  async function request(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.error || `Request failed (${response.status})`);
    return data;
  }
  function showView(name) {
    const valid = ['welcome', 'overview', 'vault', 'recall'].includes(name) ? name : 'welcome';
    document.querySelectorAll('.view').forEach((view) => view.classList.toggle('active', view.id === `view-${valid}`));
    document.querySelectorAll('[data-go]').forEach((button) => button.classList.toggle('active', button.classList.contains('nav-item') && button.dataset.go === valid));
    if (location.hash !== `#${valid}`) history.replaceState(null, '', `#${valid}`);
    window.scrollTo({ top: 0, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }
  function setTheme(theme) {
    state.theme = theme;
    document.body.dataset.theme = theme;
    document.documentElement.dataset.theme = theme;
    text($('themeGlyph'), theme === 'dark' ? '☼' : '☾');
    text($('themeLabel'), theme === 'dark' ? 'Light mode' : 'Dark mode');
    if ($('themeToggle')) $('themeToggle').setAttribute('aria-label', `Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`);
    try { localStorage.setItem('memoryflow-theme', theme); } catch (_) {}
  }
  async function refreshHealth() {
    try {
      const data = await request('/health');
      const mapping = [ ['omi', 'Omi', data.omi, data.omi === 'configured' ? 'API key configured · live access not verified' : 'API key not configured'], ['qdrant', 'Qdrant', data.qdrant, data.qdrant === 'connected' ? 'Connected to memory store' : 'Not connected'], ['lyzr', 'Lyzr', data.lyzr, data.lyzr === 'configured' ? 'API key configured · live access not verified' : 'API key not configured'] ];
      mapping.forEach(([key, label, value, description]) => { text($(`${key}Status`), description); const dot = $(`${key}Dot`); if (dot) dot.className = 'status-dot ' + (key === 'qdrant' ? (value === 'connected' ? 'ok' : 'warn') : (value === 'configured' ? 'warn' : '')); });
      text($('apiStatus'), 'API online'); if ($('apiDot')) $('apiDot').className = 'status-dot ok';
    } catch (error) {
      ['omi','qdrant','lyzr'].forEach(k => { text($(`${k}Status`), 'Status unavailable'); if ($(`${k}Dot`)) $(`${k}Dot`).className = 'status-dot warn'; });
      text($('apiStatus'), 'API unavailable'); if ($('apiDot')) $('apiDot').className = 'status-dot warn';
    }
  }
  async function saveMemory() {
    if (state.busy) return;
    const value = $('memoryInput').value.trim();
    if (!value) return status('saveStatus', 'Enter a fact to remember first.', 'error');
    state.busy = true; $('saveMemory').disabled = true; text($('saveMemory'), 'Saving memory…'); status('saveStatus', 'Sending memory to the backend…');
    try {
      const data = await request('/process', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: `Remember that ${value}` }) });
      if (data.intent !== 'remember') throw new Error(data.message || data.answer || 'The backend did not confirm a memory save.');
      status('saveStatus', '✓ Memory stored. Continue to Recall Studio and ask about this fact.', 'success');
      $('questionInput').value = 'What is the backup meeting room for the MemoryFlow demo?';
    } catch (error) { status('saveStatus', `Could not save memory: ${error.message}`, 'error'); }
    finally { state.busy = false; $('saveMemory').disabled = false; $('saveMemory').innerHTML = 'Save to memory <span>↗</span>'; }
  }
  function renderEvidence(results) {
    const list = $('evidenceList'); list.replaceChildren();
    const items = Array.isArray(results) ? results : [];
    text($('evidenceCount'), `${items.length} ${items.length === 1 ? 'item' : 'items'}`);
    items.forEach((item) => {
      const card = document.createElement('article'); card.className = 'evidence-item';
      const meta = document.createElement('div'); meta.className = 'evidence-meta';
      const source = document.createElement('span'); source.textContent = item.source || 'memory';
      const score = document.createElement('span'); score.className = 'evidence-score'; score.textContent = Number.isFinite(Number(item.score)) ? `Relevance ${Math.round(Number(item.score) * 100)}%` : '';
      meta.append(source, score); const body = document.createElement('p'); body.textContent = item.text || '';
      card.append(meta, body); list.append(card);
    });
    if (!items.length) { const empty = document.createElement('p'); empty.className = 'field-help'; empty.textContent = 'No relevant memories were returned for this question.'; list.append(empty); }
  }
  async function askMemory() {
    if (state.busy) return;
    const question = $('questionInput').value.trim();
    if (!question) { status('queryStatus', 'Enter a question to search your memory.', 'error'); return; }
    state.busy = true; $('askMemory').disabled = true; $('answerPlaceholder').hidden = true; $('answerContent').hidden = true; text($('answerState'), 'SEARCHING MEMORY…'); status('queryStatus', 'Retrieving relevant context…');
    try {
      const data = await request('/process', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: question }) });
      text($('intentBadge'), `Intent · ${data.intent || 'answer'}`);
      text($('answerText'), data.answer || data.action || data.message || 'No answer was returned.');
      renderEvidence(data.results); $('answerContent').hidden = false; text($('answerState'), 'RESPONSE READY'); status('queryStatus', 'Response received from MemoryFlow.', 'success');
    } catch (error) { $('answerPlaceholder').hidden = false; text($('answerState'), 'REQUEST FAILED'); status('queryStatus', `Could not retrieve an answer: ${error.message}`, 'error'); }
    finally { state.busy = false; $('askMemory').disabled = false; $('askMemory').innerHTML = 'Recall answer <span>↗</span>'; }
  }
  async function syncOmi() {
    if (state.busy) return;
    state.busy = true;
    const btn = $('syncOmi'); if (btn) { btn.disabled = true; text(btn, 'Syncing Omi…'); }
    text($('omiSyncStatus'), 'Contacting Omi and importing available conversations…');
    try { const data = await request('/omi/sync', { method: 'POST' }); text($('omiSyncStatus'), `${Number(data.synced) || 0} memories synced from Omi.`, 'success'); }
    catch (error) { text($('omiSyncStatus'), `Omi sync failed: ${error.message}`); $('omiSyncStatus').className = 'inline-status error'; }
    finally { state.busy = false; if (btn) { btn.disabled = false; btn.innerHTML = 'Sync Omi memories ↻'; } await refreshHealth(); }
  }
  document.addEventListener('click', (event) => {
    const go = event.target.closest('[data-go]'); if (go) { event.preventDefault(); showView(go.dataset.go); }
    const question = event.target.closest('[data-question]'); if (question) { $('questionInput').value = question.dataset.question; showView('recall'); }
  });
  $('themeToggle').addEventListener('click', () => setTheme(state.theme === 'dark' ? 'light' : 'dark'));
  $('saveMemory').addEventListener('click', saveMemory); $('askMemory').addEventListener('click', askMemory); $('syncOmi').addEventListener('click', syncOmi); $('refreshHealth').addEventListener('click', refreshHealth);
  $('questionInput').addEventListener('keydown', (event) => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') askMemory(); });
  try { const saved = localStorage.getItem('memoryflow-theme'); setTheme(saved === 'light' ? 'light' : 'dark'); } catch (_) { setTheme('dark'); }
  showView(location.hash.slice(1) || 'welcome'); refreshHealth();
})();
