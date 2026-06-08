// Human Annotator Agreement Review Tool — Darija Pilot

const state = {
  data: null,
  currentFile: null,
  wavesurfer: null,
  annotations: {},  // { [fileId]: { _correction, _note, votes: {dimKey→value}, custom: {dimKey→text} } }
  reviewer: '',
  fontSize: 12,
};

const LS_REVIEWER    = 'asr_reviewer';
const LS_ANNOTATIONS = 'asr_review_annotations';
const LS_FONT_SIZE   = 'asr_font_size';
const FONT_SIZE_DEFAULT = 12;
const FONT_SIZE_MIN     = 8;
const FONT_SIZE_MAX     = 22;
const FONT_SIZE_STEP    = 1;

// Metadata dimensions shown in the comparison grid, in display order.
// refKey: key on the file object to use as the reference value (null = show —).
const METADATA_DIMS = [
  { key: 'speaker_accent',       label: 'Speaker Accent',  refKey: 'region' },
  { key: 'register',             label: 'Register',         refKey: null },
  { key: 'loan_word_languages',  label: 'Loan Words',       refKey: null },
  { key: 'genders_present',      label: 'Genders Present',  refKey: null },
  { key: 'speaker_count',        label: 'Speaker Count',    refKey: null },
];

// ─── Init ─────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', init);

async function init() {
  initReviewer();
  loadAnnotations();
  initFontSize();

  setStatus('Loading data…');

  try {
    const r = await fetch('data_humans.json');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    state.data = await r.json();
  } catch (err) {
    showFatalError(`Failed to load data_humans.json: ${err.message}`);
    return;
  }

  populateTiers();
  const locationSelect = el('location-select');
  populateFiles(locationSelect.value);

  // Navigation
  locationSelect.addEventListener('change', () => populateFiles(locationSelect.value));
  el('file-select').addEventListener('change', e => selectFile(e.target.value));

  // Annotations — correction, notes, export/import
  el('correction-text').addEventListener('input', saveCorrection);
  el('file-notes').addEventListener('input', saveNotes);
  el('export-btn').addEventListener('click', exportAnnotations);
  el('import-input').addEventListener('change', importAnnotations);

  // Font size
  el('font-shrink-btn').addEventListener('click', () => adjustFontSize(-FONT_SIZE_STEP));
  el('font-enlarge-btn').addEventListener('click', () => adjustFontSize(FONT_SIZE_STEP));

  // Audio
  el('play-btn').addEventListener('click', () => state.wavesurfer?.playPause());

  // Metadata voting and write-in inputs (event delegation on stable parent)
  el('metadata-body').addEventListener('click', handleChipClick);
  el('metadata-body').addEventListener('input', handleCustomInput);
}

// ─── Reviewer ─────────────────────────────────────────────────────────────────

function initReviewer() {
  state.reviewer = localStorage.getItem(LS_REVIEWER) || '';
  if (!state.reviewer) {
    const name = prompt('Welcome! Please enter your name or initials to get started:');
    state.reviewer = (name || '').trim() || 'anonymous';
    localStorage.setItem(LS_REVIEWER, state.reviewer);
  }
  el('reviewer-label').textContent = state.reviewer;
}

// ─── Font Size ────────────────────────────────────────────────────────────────

function initFontSize() {
  const saved = parseInt(localStorage.getItem(LS_FONT_SIZE), 10);
  state.fontSize = (saved >= FONT_SIZE_MIN && saved <= FONT_SIZE_MAX) ? saved : FONT_SIZE_DEFAULT;
  applyFontSize();
}

function applyFontSize() {
  document.documentElement.style.setProperty('--transcription-font-size', `${state.fontSize}px`);
  el('font-size-display').textContent = `${state.fontSize}px`;
  el('font-shrink-btn').disabled = state.fontSize <= FONT_SIZE_MIN;
  el('font-enlarge-btn').disabled = state.fontSize >= FONT_SIZE_MAX;
}

function adjustFontSize(delta) {
  state.fontSize = Math.max(FONT_SIZE_MIN, Math.min(FONT_SIZE_MAX, state.fontSize + delta));
  localStorage.setItem(LS_FONT_SIZE, state.fontSize);
  applyFontSize();
}

// ─── Annotation Persistence ───────────────────────────────────────────────────

function loadAnnotations() {
  try {
    const raw = localStorage.getItem(LS_ANNOTATIONS);
    if (raw) state.annotations = JSON.parse(raw);
  } catch { state.annotations = {}; }
}

function persistAnnotations() {
  localStorage.setItem(LS_ANNOTATIONS, JSON.stringify(state.annotations));
}

function ensureFileEntry(fileId) {
  if (!state.annotations[fileId]) state.annotations[fileId] = {};
}

function ensureVotes(fileId) {
  ensureFileEntry(fileId);
  if (!state.annotations[fileId].votes)  state.annotations[fileId].votes  = {};
  if (!state.annotations[fileId].custom) state.annotations[fileId].custom = {};
}

// Toggles the vote for (fileId, dimKey): clicking the current voted value deselects it.
function saveVote(fileId, dimKey, value) {
  ensureVotes(fileId);
  const current = state.annotations[fileId].votes[dimKey];
  state.annotations[fileId].votes[dimKey] = (current === value) ? null : value;
  persistAnnotations();
}

function saveCustom(fileId, dimKey, text) {
  ensureVotes(fileId);
  state.annotations[fileId].custom[dimKey] = text;
  persistAnnotations();
}

// Reviewer transcription correction (seeded from first annotator on first visit)
function saveCorrection() {
  if (!state.currentFile) return;
  const id = state.currentFile.id;
  ensureFileEntry(id);
  state.annotations[id]._correction = el('correction-text').value;
  persistAnnotations();
}

function restoreCorrection(file) {
  const saved = state.annotations[file.id]?._correction;
  if (saved != null) {
    el('correction-text').value = saved;
    return;
  }
  // Seed with the first non-null annotator transcription
  const seed = state.data._meta.annotators
    .map(ann => file.annotations[ann]?.transcription)
    .find(t => t) || '';
  el('correction-text').value = seed;
}

// Bottom free-text notes field
function saveNotes() {
  if (!state.currentFile) return;
  const id = state.currentFile.id;
  ensureFileEntry(id);
  state.annotations[id]._note = el('file-notes').value;
  persistAnnotations();
}

function restoreNotes(file) {
  el('file-notes').value = state.annotations[file.id]?._note || '';
}

// ─── File Browser ─────────────────────────────────────────────────────────────

function populateTiers() {
  const select = el('location-select');
  select.innerHTML = '';
  for (const tier of state.data._meta.tiers) select.appendChild(makeOption(tier, tier));
}

function populateFiles(tier) {
  const select = el('file-select');
  select.innerHTML = '';
  const files = state.data.files.filter(f => f.tier === tier);
  for (const file of files) select.appendChild(makeOption(file.id, fileLabel(file)));
  if (files.length) selectFile(files[0].id);
}

function fileLabel(file) {
  return file.id.replace(/_16k$/i, '');
}

function selectFile(fileId) {
  const file = state.data.files.find(f => f.id === fileId);
  if (!file) return;
  state.currentFile = file;
  renderFile(file);
  initWaveSurfer(file);
}

// ─── Rendering ────────────────────────────────────────────────────────────────

function renderFile(file) {
  renderTranscriptions(file);
  applyWordColors();
  restoreCorrection(file);
  renderMetadata(file);
  restoreNotes(file);
}

function renderTranscriptions(file) {
  const tbody = el('transcription-body');
  tbody.innerHTML = '';

  for (const annotator of state.data._meta.annotators) {
    const ann = file.annotations[annotator];

    const tr = document.createElement('tr');
    tr.dataset.annotator = annotator;

    const labelTd = document.createElement('td');
    labelTd.className = 'system-label';
    labelTd.textContent = anonLabel(annotator);

    const textTd = document.createElement('td');
    textTd.className = 'transcription-cell';
    textTd.setAttribute('dir', 'rtl');
    textTd.dataset.annotator = annotator;
    textTd.dataset.fileId = file.id;
    renderTextSpans(textTd, ann?.transcription || null);

    tr.appendChild(labelTd);
    tr.appendChild(textTd);
    tbody.appendChild(tr);
  }
}

// ─── Annotator Display Labels ─────────────────────────────────────────────────

// Returns the shortest prefix of `name` that is unique among all annotator
// names in the loaded dataset, prepended with the anonymising handle.
// e.g. ["Bouazza","Imrane","Yassine"] → "UristMc00B", "UristMc00I", "UristMc00Y"
function anonLabel(name) {
  const all = state.data._meta.annotators;
  let len = 1;
  while (len < name.length) {
    const prefix = name.slice(0, len).toLowerCase();
    if (!all.some(n => n !== name && n.toLowerCase().startsWith(prefix))) break;
    len++;
  }
  return 'UristMc00' + name.slice(0, len);
}

// ─── Metadata Comparison Grid (M3) ───────────────────────────────────────────

function agreementStatus(values) {
  const present = values.filter(v => v != null);
  if (present.length < 2) return 'neutral';
  return new Set(present).size === 1 ? 'agree' : 'disagree';
}

function appendTh(tr, text, className) {
  const th = document.createElement('th');
  th.className = className;
  th.textContent = text;
  tr.appendChild(th);
}

function renderMetadata(file) {
  const annotators = state.data._meta.annotators;

  // Header row
  const thead = el('metadata-head');
  thead.innerHTML = '';
  const hr = document.createElement('tr');
  appendTh(hr, '',         'meta-dim-col');
  appendTh(hr, 'Region',   'meta-ref-col');
  for (const ann of annotators) appendTh(hr, anonLabel(ann), 'meta-ann-col');
  appendTh(hr, 'Write-in', 'meta-custom-col');
  thead.appendChild(hr);

  // Pre-fetch saved votes and write-in values for this file
  const savedVotes  = state.annotations[file.id]?.votes  || {};
  const savedCustom = state.annotations[file.id]?.custom || {};

  // Data rows
  const tbody = el('metadata-body');
  tbody.innerHTML = '';

  for (const dim of METADATA_DIMS) {
    const annValues = annotators.map(ann => file.annotations[ann]?.[dim.key] ?? null);
    const status    = agreementStatus(annValues);

    const tr = document.createElement('tr');
    tr.className = `meta-row meta-row-${status}`;

    // Dimension label
    const dimTd = document.createElement('td');
    dimTd.className = 'meta-dim-label';
    dimTd.textContent = dim.label;
    tr.appendChild(dimTd);

    // Reference value (only meaningful for speaker_accent → shows file.region)
    const refTd = document.createElement('td');
    refTd.className = 'meta-ref-cell';
    refTd.textContent = dim.refKey ? (file[dim.refKey] ?? '—') : '—';
    tr.appendChild(refTd);

    // One clickable chip per annotator
    for (const val of annValues) {
      const td = document.createElement('td');
      td.className = 'meta-ann-cell';

      if (val != null) {
        const btn         = document.createElement('button');
        btn.className     = 'label-chip';
        btn.dataset.dim   = dim.key;
        btn.dataset.value = val;
        btn.textContent   = val;
        if (savedVotes[dim.key] === val) btn.classList.add('voted');
        td.appendChild(btn);
      } else {
        td.classList.add('meta-missing');
        td.textContent = '—';
      }

      tr.appendChild(td);
    }

    // Write-in free-text fallback cell
    const customTd     = document.createElement('td');
    customTd.className = 'meta-custom-cell';
    const area         = document.createElement('textarea');
    area.className     = 'meta-custom-input';
    area.dataset.dim   = dim.key;
    area.rows          = 1;
    area.placeholder   = '…';
    area.value         = savedCustom[dim.key] || '';
    customTd.appendChild(area);
    tr.appendChild(customTd);

    tbody.appendChild(tr);
  }

  applyMetadataColors();
}

// ─── Agreement Voting (M4) ────────────────────────────────────────────────────

function handleChipClick(e) {
  const chip = e.target.closest('.label-chip');
  if (!chip || !state.currentFile) return;
  saveVote(state.currentFile.id, chip.dataset.dim, chip.dataset.value);
  // Refresh voted state for every chip in this row
  const row = chip.closest('tr');
  if (row) updateRowChipStates(row, state.currentFile.id);
}

// Applies .voted class to all chips in `tr` whose data-value matches the saved vote.
function updateRowChipStates(tr, fileId) {
  const votes = state.annotations[fileId]?.votes || {};
  for (const chip of tr.querySelectorAll('.label-chip')) {
    chip.classList.toggle('voted', votes[chip.dataset.dim] === chip.dataset.value);
  }
}

function handleCustomInput(e) {
  const area = e.target.closest('.meta-custom-input');
  if (!area || !state.currentFile) return;
  saveCustom(state.currentFile.id, area.dataset.dim, area.value);
}

function renderTextSpans(cell, text) {
  if (!text) { cell.textContent = '—'; return; }
  const words = text.split(/\s+/).filter(Boolean);
  if (!words.length) { cell.textContent = '—'; return; }
  const frag = document.createDocumentFragment();
  words.forEach((word, i) => {
    const span = document.createElement('span');
    span.className = 'word';
    span.textContent = word;
    frag.appendChild(span);
    if (i < words.length - 1) frag.appendChild(document.createTextNode(' '));
  });
  cell.appendChild(frag);
}

function applyWordColors() {
  const colorMap = new Map();
  let nextIndex = 0;
  for (const span of document.querySelectorAll('.transcription-cell .word')) {
    const word = span.textContent;
    if (!colorMap.has(word)) colorMap.set(word, nextIndex++);
    const hue = (colorMap.get(word) * 137.508) % 360;
    span.style.backgroundColor = `hsl(${hue}, 58%, 87%)`;
  }
}

// Per-row value coloring: within each metadata row, identical values share a hue.
// Missing (—) cells are left uncolored.
function applyMetadataColors() {
  for (const tr of el('metadata-body').querySelectorAll('tr')) {
    const isAgree = tr.classList.contains('meta-row-agree');
    const colorMap = new Map();
    let nextIndex = 0;
    for (const cell of tr.querySelectorAll('.meta-ann-cell')) {
      const chip = cell.querySelector('.label-chip');
      if (!chip) { cell.style.backgroundColor = ''; continue; }
      if (isAgree) {
        cell.style.backgroundColor = 'hsl(142, 45%, 88%)';
      } else {
        const val = chip.dataset.value;
        if (!colorMap.has(val)) colorMap.set(val, nextIndex++);
        const hue = (colorMap.get(val) * 137.508) % 360;
        cell.style.backgroundColor = `hsl(${hue}, 58%, 87%)`;
      }
    }
  }
}

// ─── Audio ────────────────────────────────────────────────────────────────────

function initWaveSurfer(file) {
  if (state.wavesurfer) { state.wavesurfer.destroy(); state.wavesurfer = null; }

  const playBtn = el('play-btn');
  playBtn.disabled = true;
  playBtn.textContent = '▶ Play';

  const container = el('waveform-container');
  container.innerHTML = '<div id="waveform-placeholder">Decoding audio…</div>';
  const waveDiv = document.createElement('div');
  waveDiv.id = 'waveform-wave';
  container.appendChild(waveDiv);

  state.wavesurfer = WaveSurfer.create({
    container: waveDiv,
    waveColor: '#475569', progressColor: '#3b82f6',
    height: parseInt(getComputedStyle(document.documentElement)
              .getPropertyValue('--waveform-height'), 10) || 96,
    barWidth: 2, barGap: 1, barRadius: 2, normalize: true,
  });

  waveDiv.addEventListener('pointerdown', e => {
    if (!state.wavesurfer) return;
    const rect = waveDiv.getBoundingClientRect();
    state.wavesurfer.seekTo(Math.max(0, Math.min(1, 1 - ((e.clientX - rect.left) / rect.width))));
    e.stopImmediatePropagation();
  }, true);

  state.wavesurfer.on('ready',      () => { document.getElementById('waveform-placeholder')?.remove(); playBtn.disabled = false; });
  state.wavesurfer.on('play',       () => { playBtn.textContent = '⏸ Pause'; });
  state.wavesurfer.on('pause',      () => { playBtn.textContent = '▶ Play'; });
  state.wavesurfer.on('finish',     () => { playBtn.textContent = '▶ Play'; });
  state.wavesurfer.on('error',      err => {
    console.error('WaveSurfer error:', err);
    container.innerHTML = '<div id="waveform-error">⚠ Audio file could not be loaded.</div>';
    playBtn.disabled = true;
  });

  state.wavesurfer.load(file.audio_path);
}

// ─── Export / Import ──────────────────────────────────────────────────────────

function exportAnnotations() {
  const date     = new Date().toISOString().slice(0, 10);
  const filename = `agreement_votes_${state.reviewer}_${date}.json`;
  const blob     = new Blob([JSON.stringify(state.annotations, null, 2)], { type: 'application/json' });
  const url      = URL.createObjectURL(blob);
  const a        = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

function importAnnotations(e) {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = ev => {
    try {
      const imported = JSON.parse(ev.target.result);
      for (const [fileId, fileData] of Object.entries(imported)) {
        state.annotations[fileId] = Object.assign(state.annotations[fileId] || {}, fileData);
      }
      persistAnnotations();
      if (state.currentFile) renderFile(state.currentFile);
      alert('Annotations imported successfully.');
    } catch { alert('Import failed: the file does not appear to be valid JSON.'); }
  };
  reader.readAsText(file);
  e.target.value = '';
}

// ─── Utilities ────────────────────────────────────────────────────────────────

function el(id) { return document.getElementById(id); }

function makeOption(value, label) {
  const opt = document.createElement('option');
  opt.value = value; opt.textContent = label;
  return opt;
}

function setStatus(msg) {
  el('transcription-body').innerHTML = `<tr><td colspan="2" class="status-cell">${msg}</td></tr>`;
}

function showFatalError(msg) {
  el('app').innerHTML = `<div class="fatal-error">${msg}</div>`;
}
