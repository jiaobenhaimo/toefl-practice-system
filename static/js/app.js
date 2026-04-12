/**
 * app.js — TOEFL Practice Test Engine
 *
 * Supports two modes:
 *   - Section mode: take one module
 *   - Full test mode: chain all modules in order (reading → listening → writing → speaking)
 *
 * Features: question rendering, navigation, audio, mic recording,
 * timer, auto-save, auto-grading, zip download.
 */

/* ======= STATE ======= */
let playlist = [];          // Ordered list of modules to take
let playlistIdx = 0;        // Current index into playlist
let currentModule = null;   // Current module runtime data
let currentPageIdx = 0;
let answers = {};           // Current module answers: { qid: value }
let recordings = {};        // Current module recordings: { qid: Blob }
let allResults = [];        // Accumulated results from finished modules
let timerInterval = null;
let timerSecondsLeft = 0;
let questionTimerInterval = null;
let autoSaveInterval = null;
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let isFinishing = false;    // Guard against double-triggering finishCurrentModule
let playedAudio = new Set();    // Track audio clips already played (for no-replay mode)

/* ======= AUDIO CODEC DETECTION ======= */
const AUDIO_TYPES = [
    { mimeType: 'audio/webm;codecs=opus', ext: 'webm' },
    { mimeType: 'audio/webm', ext: 'webm' },
    { mimeType: 'audio/mp4', ext: 'm4a' },
    { mimeType: 'audio/ogg;codecs=opus', ext: 'ogg' },
    { mimeType: 'audio/ogg', ext: 'ogg' },
];
let recordingMimeType = '';
let recordingExt = 'webm';
(function detectCodec() {
    if (typeof MediaRecorder === 'undefined') return;
    for (const t of AUDIO_TYPES) {
        if (MediaRecorder.isTypeSupported(t.mimeType)) {
            recordingMimeType = t.mimeType;
            recordingExt = t.ext;
            return;
        }
    }
})();

/* ======= STORAGE ======= */
function storageKey(suffix) {
    const mode = URL_PARAMS.mode || 'full';
    const scope = mode === 'section' ? 'sec_' + (URL_PARAMS.section || '') : 'full';
    return 'toefl_' + TEST_INFO.test_id + '_' + scope + '_' + suffix;
}

function moduleKey(mod) {
    return mod.filename + '_' + mod.module_index;
}

function safeSetItem(key, value) {
    try {
        localStorage.setItem(key, value);
    } catch (e) {
        console.warn('localStorage write failed (quota exceeded?):', e);
    }
}

function saveModuleProgress() {
    if (!currentModule) return;
    const mod = playlist[playlistIdx];
    safeSetItem(storageKey('mod_' + moduleKey(mod)), JSON.stringify({
        pageIdx: currentPageIdx,
        answers: answers,
        timerSecondsLeft: timerSecondsLeft,
        savedAt: new Date().toISOString(),
    }));
}

function savePlaylistState() {
    safeSetItem(storageKey('playlist'), JSON.stringify({
        playlist: playlist,
        playlistIdx: playlistIdx,
        allResults: allResults.map(r => ({ ...r, recordings: undefined })),
    }));
}

function loadModuleProgress(mod) {
    const raw = localStorage.getItem(storageKey('mod_' + moduleKey(mod)));
    return raw ? JSON.parse(raw) : null;
}

function loadPlaylistProgress() {
    const raw = localStorage.getItem(storageKey('playlist'));
    return raw ? JSON.parse(raw) : null;
}

function markModuleComplete(mod) {
    safeSetItem(storageKey('complete_' + moduleKey(mod)), '1');
}

function autoSave() {
    collectAnswer();
    saveModuleProgress();
}

function startAutoSave() {
    stopAutoSave();
    autoSaveInterval = setInterval(autoSave, 30000);
}

function stopAutoSave() {
    if (autoSaveInterval) clearInterval(autoSaveInterval);
    autoSaveInterval = null;
}

/* ======= SCREEN SWITCHING ======= */
let _activeScreen = null;

function showScreen(id) {
    if (_activeScreen) _activeScreen.classList.remove('screen--active');
    _activeScreen = document.getElementById(id);
    _activeScreen.classList.add('screen--active');
}

/* ======= INITIALIZATION ======= */
document.addEventListener('DOMContentLoaded', () => {
    _activeScreen = document.querySelector('.screen--active');
    const mode = URL_PARAMS.mode || 'full';

    if (mode === 'section') {
        // Section mode - chain all modules of the chosen section
        const sectionName = URL_PARAMS.section;
        const sectionMods = TEST_INFO.modules.filter(m => m.section === sectionName);
        if (!sectionMods.length) {
            alert('Section not found.');
            window.location.href = '/';
            return;
        }
        playlist = sectionMods;
        playlistIdx = 0;
        allResults = [];
        loadAndStartModule();
    } else {
        // Full test mode - chain all modules in section order
        playlist = [...TEST_INFO.modules];
        if (playlist.length === 0) {
            alert('No modules found in this test.');
            window.location.href = '/';
            return;
        }
        playlistIdx = 0;
        allResults = [];

        // Check for saved playlist progress
        const saved = loadPlaylistProgress();
        if (saved && saved.playlistIdx !== undefined) {
            const resumeIdx = saved.playlistIdx;
            // Validate: resumeIdx must be > 0 and within bounds
            if (resumeIdx > 0 && resumeIdx < playlist.length) {
                if (confirm('Resume from where you left off?')) {
                    playlistIdx = resumeIdx;
                    allResults = Array.isArray(saved.allResults) ? saved.allResults : [];
                }
            } else if (resumeIdx >= playlist.length) {
                // Test was previously completed - clear stale data
                localStorage.removeItem(storageKey('playlist'));
            }
        }
        loadAndStartModule();
    }
});

/* ======= MODULE LOADING ======= */
async function loadAndStartModule() {
    if (!playlist.length || playlistIdx >= playlist.length) {
        if (allResults.length > 0) {
            showFinalResults();
        } else {
            alert('No modules to load.');
            window.location.href = '/';
        }
        return;
    }

    const mod = playlist[playlistIdx];
    showScreen('screen-loading');

    try {
        const url = '/api/module/' + encodeURIComponent(mod.filename) + '?module_index=' + mod.module_index;
        const resp = await fetch(url);
        if (!resp.ok) throw new Error('Failed to load module');
        const data = await resp.json();

        currentModule = {
            filename: mod.filename,
            moduleIndex: mod.module_index,
            section: data.module_info.section,
            moduleNum: data.module_info.module,
            timerMinutes: data.module_info.timer_minutes,
            pages: data.pages,
            audioDir: mod.filename.replace('.md', ''),
        };

        // Restore progress or start fresh
        const saved = loadModuleProgress(mod);
        if (saved && saved.answers) {
            answers = saved.answers;
            currentPageIdx = saved.pageIdx ?? 0;
            // If timer was saved as 0 or negative (expired), use full time
            const restoredTime = saved.timerSecondsLeft;
            timerSecondsLeft = (restoredTime != null && restoredTime > 0) ? restoredTime : (mod.timer_minutes * 60);
        } else {
            answers = {};
            currentPageIdx = 0;
            timerSecondsLeft = mod.timer_minutes * 60;
        }
        recordings = {};
        playedAudio = new Set();
        isFinishing = false;

        // UI
        const sectionLabel = capitalize(currentModule.section);
        let label = sectionLabel + ' — Module ' + currentModule.moduleNum;
        if (playlist.length > 1) {
            label = `[${playlistIdx + 1}/${playlist.length}] ` + label;
        }
        document.getElementById('section-label').textContent = label;

        showScreen('screen-test');
        startTimer();
        startAutoSave();
        renderQuestion();
    } catch (e) {
        alert('Error loading module: ' + e.message);
        window.location.href = '/';
    }
}

async function exitModule() {
    if (confirm('Save progress and exit? You can resume later.')) {
        collectAnswer();
        saveModuleProgress();
        stopTimer();
        stopAutoSave();
        await stopRecording();
        window.location.href = '/';
    }
}

/* ======= TIMER ======= */
function startTimer() {
    stopTimer();
    updateTimerDisplay();
    if (timerSecondsLeft <= 0) {
        finishCurrentModule();
        return;
    }
    timerInterval = setInterval(() => {
        timerSecondsLeft--;
        updateTimerDisplay();
        if (timerSecondsLeft <= 0) {
            stopTimer();
            finishCurrentModule();
        }
    }, 1000);
}

function stopTimer() {
    if (timerInterval) clearInterval(timerInterval);
    timerInterval = null;
}

function updateTimerDisplay() {
    const m = Math.max(0, Math.floor(timerSecondsLeft / 60));
    const s = Math.max(0, timerSecondsLeft % 60);
    const display = document.getElementById('timer-display');
    display.textContent = String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
    const timer = document.getElementById('timer');
    if (timerSecondsLeft <= 60) {
        timer.classList.add('timer--danger');
        timer.classList.remove('timer--warning');
    } else if (timerSecondsLeft <= 300) {
        timer.classList.add('timer--warning');
        timer.classList.remove('timer--danger');
    } else {
        timer.classList.remove('timer--warning', 'timer--danger');
    }
}

/* ======= QUESTION TIMER (for speaking) ======= */
function startQuestionTimer(seconds, onFinish) {
    stopQuestionTimer();
    let left = seconds;
    const bar = document.getElementById('q-timer-bar');
    const label = document.getElementById('q-timer-label');
    if (bar) bar.style.width = '100%';
    if (label) label.textContent = seconds + 's';

    questionTimerInterval = setInterval(() => {
        left--;
        if (bar) bar.style.width = ((left / seconds) * 100) + '%';
        if (label) label.textContent = left + 's';
        if (left <= 0) {
            stopQuestionTimer();
            if (onFinish) onFinish();
        }
    }, 1000);
}

function stopQuestionTimer() {
    if (questionTimerInterval) clearInterval(questionTimerInterval);
    questionTimerInterval = null;
}

/* ======= NAVIGATION ======= */
function renderQuestion() {
    if (!currentModule || currentPageIdx >= currentModule.pages.length) return;
    const page = currentModule.pages[currentPageIdx];
    const total = currentModule.pages.length;
    const section = currentModule.section;

    document.getElementById('q-counter').textContent =
        'Question ' + (currentPageIdx + 1) + ' / ' + total;

    // Prev button: only reading
    const btnPrev = document.getElementById('btn-prev');
    btnPrev.style.display = (section === 'reading' && currentPageIdx > 0) ? '' : 'none';

    // Next button text
    const btnNext = document.getElementById('btn-next');
    if (currentPageIdx === total - 1) {
        if (playlistIdx < playlist.length - 1) {
            btnNext.textContent = 'Next Section →';
        } else {
            btnNext.textContent = 'Finish';
        }
    } else {
        btnNext.textContent = 'Next →';
    }

    // Render body
    const body = document.getElementById('test-body');
    body.innerHTML = '';
    body.classList.remove('test-body--splitpane');
    window.scrollTo(0, 0);
    body.scrollTop = 0;

    body.appendChild(el('div', 'group-label', page.group_title));

    switch (page.question_type) {
        case 'mc': renderMC(body, page); break;
        case 'cloze': renderCloze(body, page); break;
        case 'build_sentence': renderBuildSentence(body, page); break;
        case 'email': case 'discussion': renderFreeWrite(body, page); break;
        case 'listen_repeat': case 'interview': renderSpeaking(body, page); break;
    }
}

let _navigating = false;

async function nextQuestion() {
    if (_navigating) return;
    _navigating = true;
    try {
        collectAnswer();
        saveModuleProgress();
        await stopRecording();
        stopQuestionTimer();

        if (currentPageIdx >= currentModule.pages.length - 1) {
            await finishCurrentModule();
            return;
        }
        currentPageIdx++;
        renderQuestion();
    } finally {
        _navigating = false;
    }
}

function prevQuestion() {
    if (_navigating) return;
    collectAnswer();
    saveModuleProgress();
    if (currentPageIdx > 0) {
        currentPageIdx--;
        renderQuestion();
    }
}

/* ======= COLLECT ANSWER ======= */
function collectAnswer() {
    if (!currentModule) return;
    const page = currentModule.pages[currentPageIdx];
    const qid = page.question_id;

    switch (page.question_type) {
        case 'mc': {
            const sel = document.querySelector('input[name="mc-answer"]:checked');
            if (sel) answers[qid] = sel.value;
            break;
        }
        case 'cloze': {
            const groups = document.querySelectorAll('.cloze-group');
            const vals = [];
            groups.forEach(group => {
                const chars = group.querySelectorAll('.cloze-char');
                let word = '';
                chars.forEach(c => { word += c.value; });
                vals.push(word);
            });
            answers[qid] = vals;
            break;
        }
        case 'build_sentence': {
            const slots = document.querySelectorAll('#sentence-slots .word-chip--placed');
            const words = [];
            slots.forEach(chip => words.push(chip.textContent.trim()));
            answers[qid] = words.join(' ');
            break;
        }
        case 'email': case 'discussion': {
            const ta = document.getElementById('free-write-area');
            if (ta) answers[qid] = ta.value;
            break;
        }
    }
}

/* ======= RENDERERS ======= */
function el(tag, className, innerHTML) {
    const e = document.createElement(tag);
    if (className) e.className = className;
    if (innerHTML !== undefined) e.innerHTML = innerHTML;
    return e;
}

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;')
              .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function capitalize(str) {
    return str.charAt(0).toUpperCase() + str.slice(1);
}

function renderMC(body, page) {
    const hasReadingPassage = page.section === 'reading' && page.passage_html;

    if (hasReadingPassage) {
        // Split-pane: passage left, question right
        body.classList.add('test-body--splitpane');
        const pane = el('div', 'split-pane');

        const left = el('div', 'split-pane__left');
        const passageEl = el('div', 'passage-panel passage-panel--full');
        passageEl.innerHTML = page.passage_html;
        left.appendChild(passageEl);
        pane.appendChild(left);

        const right = el('div', 'split-pane__right');
        right.appendChild(el('div', 'group-label', page.group_title));
        if (page.prompt_html) {
            const p = el('div', 'question-prompt');
            p.innerHTML = page.prompt_html;
            right.appendChild(p);
        }
        const choicesEl = buildChoices(page);
        right.appendChild(choicesEl);
        pane.appendChild(right);

        // Replace the group-label already added by renderQuestion
        const existingLabel = body.querySelector('.group-label');
        if (existingLabel) existingLabel.remove();

        body.appendChild(pane);
    } else {
        body.classList.remove('test-body--splitpane');
        if (page.passage_html) {
            const p = el('div', 'passage-panel');
            p.innerHTML = page.passage_html;
            body.appendChild(p);
        }
        if (page.audio) {
            const opts = page.section === 'listening' ? { autoPlayOnce: true } : {};
            body.appendChild(createAudioPlayer(page.audio, opts));
        }
        if (page.prompt_html) {
            const p = el('div', 'question-prompt');
            p.innerHTML = page.prompt_html;
            body.appendChild(p);
        } else if (page.prompt) {
            body.appendChild(el('div', 'question-prompt', escapeHtml(page.prompt)));
        }
        const choicesEl = buildChoices(page);
        body.appendChild(choicesEl);
    }
}

function buildChoices(page) {
    const choicesEl = el('div', 'choices');
    const saved = answers[page.question_id] || '';
    for (const [letter, text] of Object.entries(page.choices)) {
        const label = document.createElement('label');
        label.className = 'choice' + (saved === letter ? ' choice--selected' : '');
        const radio = document.createElement('input');
        radio.type = 'radio'; radio.name = 'mc-answer'; radio.value = letter;
        if (saved === letter) radio.checked = true;
        radio.addEventListener('change', () => {
            choicesEl.querySelectorAll('.choice--selected').forEach(c => c.classList.remove('choice--selected'));
            label.classList.add('choice--selected');
        });
        label.appendChild(radio);
        label.appendChild(el('span', 'choice__text', '<strong>(' + letter + ')</strong> ' + escapeHtml(text)));
        choicesEl.appendChild(label);
    }
    return choicesEl;
}

function renderCloze(body, page) {
    if (!page.passage_html) return;
    let html = page.passage_html;
    const saved = answers[page.question_id] || [];
    let idx = 0;

    // Replace [N] patterns with character input groups
    // Pattern: optional prefix word chars, [digit], optional suffix word chars
    html = html.replace(/(\w*)\[(\d+)\](\w*)/g, (match, prefix, countStr, suffix) => {
        const count = parseInt(countStr);
        const savedVal = saved[idx] || '';
        let inputs = '';
        for (let c = 0; c < count; c++) {
            const charVal = savedVal[c] || '';
            inputs += '<input type="text" maxlength="1" class="cloze-char" ' +
                'data-blank="' + idx + '" data-pos="' + c + '" data-count="' + count + '" ' +
                'value="' + escapeHtml(charVal) + '" autocomplete="off" autocapitalize="off" spellcheck="false">';
        }
        const result = '<span class="cloze-group" data-idx="' + idx + '">' +
            escapeHtml(prefix) +
            '<span class="cloze-chars">' + inputs + '</span>' +
            escapeHtml(suffix) +
            '</span>';
        idx++;
        return result;
    });

    const p = el('div', 'passage-panel passage-panel--cloze');
    p.innerHTML = html;
    body.appendChild(p);
    body.appendChild(el('div', 'cloze-hint', 'Fill in all ' + idx + ' blanks, then click Next.'));

    // Wire up auto-advance behavior
    const allChars = Array.from(p.querySelectorAll('.cloze-char'));
    allChars.forEach((inp, i) => {
        inp.addEventListener('input', () => {
            if (inp.value.length === 1) {
                // Advance to next character input
                const next = allChars[i + 1] || allChars[0];
                next.focus();
                next.select();
            }
        });
        inp.addEventListener('keydown', (e) => {
            if (e.key === 'Backspace' && inp.value === '') {
                // Go to previous character input
                e.preventDefault();
                const prev = allChars[i - 1];
                if (prev) { prev.focus(); prev.select(); }
            } else if (e.key === 'ArrowRight') {
                e.preventDefault();
                const next = allChars[i + 1];
                if (next) next.focus();
            } else if (e.key === 'ArrowLeft') {
                e.preventDefault();
                const prev = allChars[i - 1];
                if (prev) prev.focus();
            }
        });
        // Select all text on focus for easy overwrite
        inp.addEventListener('focus', () => inp.select());
    });
}

function renderBuildSentence(body, page) {
    const details = page.details || {};
    if (details.context) {
        body.appendChild(el('div', 'bs-context', '<strong>Context:</strong> ' + escapeHtml(details.context)));
    }
    body.appendChild(el('div', 'bs-instruction', 'Tap words in order to build the sentence. Tap a placed word to remove it.'));

    const slotsArea = el('div', 'sentence-slots'); slotsArea.id = 'sentence-slots';
    slotsArea.setAttribute('data-empty', 'true');

    // Punctuation at the end (?, !, or .)
    const lastChar = (page.answer || '').trim().slice(-1);
    const punct = /[?!.]/.test(lastChar) ? lastChar : '.';
    const punctEl = el('span', 'sentence-punct', punct);
    punctEl.id = 'sentence-punct';
    slotsArea.appendChild(punctEl);

    body.appendChild(slotsArea);

    // Capitalize first placed chip, lowercase the rest
    function updateCapitalization() {
        const placed = slotsArea.querySelectorAll('.word-chip--placed');
        const isEmpty = placed.length === 0;
        slotsArea.setAttribute('data-empty', isEmpty ? 'true' : 'false');
        placed.forEach((chip, i) => {
            const orig = chip.getAttribute('data-word');
            if (i === 0) {
                chip.textContent = capitalize(orig);
            } else {
                chip.textContent = orig;
            }
        });
    }

    const bank = el('div', 'word-bank'); bank.id = 'word-bank';
    const words = details.words ? [...details.words] : [];
    words.sort(() => Math.random() - 0.5);
    words.forEach(word => {
        const lowerWord = word.toLowerCase();
        const chip = el('button', 'word-chip word-chip--bank', escapeHtml(lowerWord));
        chip.setAttribute('data-word', lowerWord);
        chip.addEventListener('click', () => {
            chip.classList.add('word-chip--used'); chip.disabled = true;
            const placed = el('button', 'word-chip word-chip--placed', escapeHtml(lowerWord));
            placed.setAttribute('data-word', lowerWord);
            placed.addEventListener('click', () => {
                slotsArea.removeChild(placed);
                chip.classList.remove('word-chip--used'); chip.disabled = false;
                updateCapitalization();
            });
            // Insert before the punctuation element so punct stays at the end
            slotsArea.insertBefore(placed, punctEl);
            updateCapitalization();
        });
        bank.appendChild(chip);
    });
    body.appendChild(bank);

    // Restore saved answer
    const saved = answers[page.question_id];
    if (saved) {
        let remaining = saved.replace(/[?!.]$/, '').trim().toLowerCase();
        const availableChips = Array.from(bank.querySelectorAll('.word-chip--bank'));
        while (remaining.length > 0) {
            let matched = false;
            const unused = availableChips.filter(b => !b.classList.contains('word-chip--used'));
            unused.sort((a, b) => b.textContent.trim().length - a.textContent.trim().length);
            for (const btn of unused) {
                const chipText = btn.getAttribute('data-word');
                if (remaining.startsWith(chipText)) {
                    btn.click();
                    remaining = remaining.slice(chipText.length).trimStart();
                    matched = true;
                    break;
                }
            }
            if (!matched) break;
        }
    }
}

function renderFreeWrite(body, page) {
    if (page.content_html) {
        const c = el('div', 'write-prompt');
        c.innerHTML = page.content_html;
        body.appendChild(c);
    }
    if (page.time_minutes) {
        body.appendChild(el('div', 'write-time-hint', 'Suggested time: ' + page.time_minutes + ' minutes'));
    }
    const ta = document.createElement('textarea');
    ta.id = 'free-write-area'; ta.className = 'free-write-area';
    ta.placeholder = 'Type your response here...'; ta.rows = 14;
    ta.value = answers[page.question_id] || '';
    body.appendChild(ta);
    const wc = el('div', 'word-count', '0 words');
    body.appendChild(wc);
    ta.addEventListener('input', () => {
        const count = ta.value.trim() ? ta.value.trim().split(/\s+/).length : 0;
        wc.textContent = count + ' words';
    });
    ta.dispatchEvent(new Event('input'));
}

function renderSpeaking(body, page) {
    body.appendChild(el('div', 'speak-instruction', page.content || 'Listen and respond.'));

    const recBtn = el('button', 'btn btn--record btn--record-waiting', 'Listen to the prompt first...');
    recBtn.id = 'btn-record';
    recBtn.disabled = true;

    // Enable record button after audio finishes (or immediately if no audio / already played)
    function enableRecording() {
        recBtn.disabled = false;
        recBtn.textContent = 'Start Recording';
        recBtn.classList.remove('btn--record-waiting');
        recBtn.classList.add('btn--record-ready');
    }

    if (page.audio) {
        const alreadyPlayed = playedAudio.has(page.audio);
        body.appendChild(createAudioPlayer(page.audio, {
            autoPlayOnce: true,
            onEnded: enableRecording,
        }));
        if (alreadyPlayed) enableRecording();
    } else {
        enableRecording();
    }

    const timerWrap = el('div', 'q-timer-wrap');
    timerWrap.innerHTML = '<div class="q-timer-track"><div class="q-timer-bar" id="q-timer-bar"></div></div>' +
        '<span class="q-timer-label" id="q-timer-label">--</span>';
    body.appendChild(timerWrap);

    const recStatus = el('div', 'rec-status', ''); recStatus.id = 'rec-status';
    const playback = el('div', 'rec-playback'); playback.id = 'rec-playback';
    if (recordings[page.question_id]) showPlayback(playback, recordings[page.question_id]);
    recBtn.addEventListener('click', async () => {
        if (isRecording) {
            stopRecordingAndSave(page.question_id, playback);
        } else {
            try {
                if (!recordingMimeType) {
                    recStatus.textContent = 'Recording not supported in this browser.';
                    return;
                }
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                mediaRecorder = new MediaRecorder(stream, { mimeType: recordingMimeType });
                audioChunks = [];
                mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
                mediaRecorder.onstop = () => {
                    const blob = new Blob(audioChunks, { type: recordingMimeType });
                    recordings[page.question_id] = blob;
                    answers[page.question_id] = '[audio recorded]';
                    stream.getTracks().forEach(t => t.stop());
                    showPlayback(playback, blob);
                    recStatus.textContent = 'Recording saved.';
                    recBtn.textContent = 'Re-record';
                    recBtn.classList.remove('btn--recording');
                    isRecording = false;
                };
                mediaRecorder.start();
                isRecording = true;
                recBtn.textContent = 'Stop Recording';
                recBtn.classList.add('btn--recording');
                recStatus.textContent = 'Recording...';
                playback.innerHTML = '';
                startQuestionTimer(page.time_seconds ?? 30, () => {
                    if (isRecording) stopRecordingAndSave(page.question_id, playback);
                });
            } catch (err) {
                recStatus.textContent = 'Microphone access denied.';
            }
        }
    });
    body.appendChild(recBtn); body.appendChild(recStatus); body.appendChild(playback);
}

let _pendingStop = null; // Shared Promise for in-progress recording stop

function stopRecordingAndSave() {
    stopQuestionTimer();
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        // Start the stop — the shared promise lets stopRecording() await it too
        _pendingStop = new Promise(resolve => {
            const origOnStop = mediaRecorder.onstop;
            mediaRecorder.onstop = (e) => {
                if (origOnStop) origOnStop(e);
                isRecording = false;
                _pendingStop = null;
                resolve();
            };
            mediaRecorder.stop();
        });
    }
    isRecording = false;
}

function stopRecording() {
    stopQuestionTimer();
    // If a stop is already in progress (e.g. timer expired), wait for it
    if (_pendingStop) return _pendingStop;
    if (!isRecording || !mediaRecorder || mediaRecorder.state === 'inactive') {
        isRecording = false;
        return Promise.resolve();
    }
    _pendingStop = new Promise(resolve => {
        const origOnStop = mediaRecorder.onstop;
        mediaRecorder.onstop = (e) => {
            if (origOnStop) origOnStop(e);
            isRecording = false;
            _pendingStop = null;
            resolve();
        };
        mediaRecorder.stop();
    });
    isRecording = false;
    return _pendingStop;
}

function showPlayback(container, blob) {
    // Revoke previous object URL to prevent memory leak
    const prevAudio = container.querySelector('audio');
    if (prevAudio && prevAudio.src.startsWith('blob:')) {
        URL.revokeObjectURL(prevAudio.src);
    }
    container.innerHTML = '';
    const audio = document.createElement('audio');
    audio.controls = true; audio.src = URL.createObjectURL(blob);
    container.appendChild(audio);
}

function createAudioPlayer(src, options) {
    const autoPlayOnce = options && options.autoPlayOnce;
    const onEnded = options && options.onEnded;
    const wrap = el('div', 'audio-player');
    const note = el('div', 'audio-note', '');
    wrap.appendChild(note);

    if (autoPlayOnce) {
        // Already played — show disabled state, no audio element
        if (playedAudio.has(src)) {
            note.textContent = 'Audio already played';
            note.className = 'audio-note audio-note--played';
            return wrap;
        }

        const audioEl = document.createElement('audio');
        audioEl.preload = 'auto';
        audioEl.src = '/audio/' + currentModule.audioDir + '/' + src + '.ogg';
        audioEl.controls = false;
        wrap.appendChild(audioEl);

        // Show progress bar while playing
        const progressWrap = el('div', 'audio-progress-wrap');
        const progressBar = el('div', 'audio-progress-bar');
        progressWrap.appendChild(progressBar);
        wrap.appendChild(progressWrap);

        audioEl.addEventListener('timeupdate', () => {
            if (audioEl.duration) {
                progressBar.style.width = ((audioEl.currentTime / audioEl.duration) * 100) + '%';
            }
        });

        audioEl.addEventListener('ended', () => {
            note.textContent = 'Audio played (no replay)';
            note.className = 'audio-note audio-note--played';
            audioEl.remove();
            progressWrap.remove();
            if (onEnded) onEnded();
        });

        audioEl.addEventListener('error', () => {
            note.innerHTML = 'Audio file not found: <code>' + src + '.ogg</code>';
            note.classList.add('audio-note--error');
            if (onEnded) onEnded();
        });

        // Attempt auto-play
        note.textContent = 'Playing...';
        const playPromise = audioEl.play();
        if (playPromise !== undefined) {
            playPromise.then(() => {
                playedAudio.add(src);
            }).catch(() => {
                // Browser blocked auto-play — show manual play button
                note.textContent = '';
                progressWrap.style.display = 'none';
                const playBtn = el('button', 'btn btn--primary audio-play-btn', 'Click to Play Audio');
                playBtn.addEventListener('click', () => {
                    audioEl.play();
                    playedAudio.add(src);
                    playBtn.remove();
                    note.textContent = 'Playing...';
                    progressWrap.style.display = '';
                }, { once: true });
                note.appendChild(playBtn);
            });
        }
    } else {
        // Normal mode — controls visible, replayable
        const audioEl = document.createElement('audio');
        audioEl.preload = 'auto';
        audioEl.src = '/audio/' + currentModule.audioDir + '/' + src + '.ogg';
        audioEl.controls = true;
        note.textContent = 'Audio: ' + src + '.ogg';
        wrap.appendChild(audioEl);
        audioEl.addEventListener('error', () => {
            note.innerHTML = 'Audio file not found: <code>' + src + '.ogg</code>';
            note.classList.add('audio-note--error');
        });
    }

    return wrap;
}

/* ======= FINISH CURRENT MODULE ======= */
async function finishCurrentModule() {
    if (isFinishing) return;
    isFinishing = true;

    collectAnswer();
    stopTimer();
    stopAutoSave();
    await stopRecording();
    stopQuestionTimer();

    // Grade this module and store results
    const result = gradeModule(currentModule, answers, recordings);
    allResults.push(result);

    // Mark complete, clear per-module progress
    const mod = playlist[playlistIdx];
    markModuleComplete(mod);
    localStorage.removeItem(storageKey('mod_' + moduleKey(mod)));

    // Clear current module so stale data isn't saved
    currentModule = null;

    // Advance playlist
    playlistIdx++;

    if (playlistIdx < playlist.length) {
        savePlaylistState();
        const nextMod = playlist[playlistIdx];
        // If next module is same section, go straight to it (no transition)
        if (nextMod.section === result.section) {
            await loadAndStartModule();
        } else {
            // Different section — show transition screen
            const doneSec = capitalize(result.section);
            const nextSec = capitalize(nextMod.section);
            document.getElementById('transition-done').textContent = doneSec + ' complete';
            document.getElementById('transition-next').textContent =
                'Next: ' + nextSec + ' — Module ' + nextMod.module + ' (' + nextMod.timer_minutes + ' min)';
            showScreen('screen-transition');
        }
    } else {
        localStorage.removeItem(storageKey('playlist'));
        showFinalResults();
    }
}

/* ======= GRADING ======= */
function gradeModule(mod, ans, recs) {
    const section = mod.section;
    const pages = mod.pages;
    let correct = 0, total = 0;
    const details = [];
    let hasDownloadable = false;

    pages.forEach(page => {
        const qid = page.question_id;
        const userAns = ans[qid];

        if (page.question_type === 'mc') {
            total++;
            const ok = userAns === page.answer;
            if (ok) correct++;
            details.push({ qid, type: 'mc', correct: ok, user: userAns || '—', expected: page.answer });
        } else if (page.question_type === 'cloze') {
            const fills = page.cloze_fills || page.cloze_answers || [];
            const fullWords = page.cloze_answers || [];
            const ua = userAns || [];
            fills.forEach((expected_fill, i) => {
                total++;
                const uv = (ua[i] || '').trim();
                const ok = uv.toLowerCase() === expected_fill.toLowerCase();
                if (ok) correct++;
                details.push({
                    qid: qid + '.' + (i + 1), type: 'cloze', correct: ok,
                    user: uv || '—',
                    expected: expected_fill,
                    fullWord: fullWords[i] || expected_fill,
                });
            });
        } else if (page.question_type === 'build_sentence') {
            total++;
            const exp = (page.answer || '').trim().toLowerCase().replace(/[?!.]/g, '');
            const usr = (userAns || '').trim().toLowerCase().replace(/[?!.]/g, '');
            const ok = usr === exp;
            if (ok) correct++;
            details.push({ qid, type: 'build_sentence', correct: ok, user: userAns || '—', expected: page.answer });
        } else if (page.question_type === 'email' || page.question_type === 'discussion') {
            hasDownloadable = true;
            const wc = (userAns || '').trim().split(/\s+/).filter(Boolean).length;
            details.push({ qid, type: page.question_type, user: userAns, wordCount: wc });
        } else if (page.question_type === 'listen_repeat' || page.question_type === 'interview') {
            hasDownloadable = true;
            details.push({ qid, type: page.question_type, hasRecording: !!recs[qid] });
        }
    });

    return {
        section, moduleNum: mod.moduleNum,
        score: { correct, total },
        details,
        hasDownloadable,
        answers: { ...ans },
        recordings: { ...recs },
    };
}

/* ======= FINAL RESULTS SCREEN ======= */
function showFinalResults() {
    showScreen('screen-results');

    const isFull = playlist.length > 1;
    document.getElementById('results-title').textContent = isFull ? 'Full Test Results' : 'Results';

    let html = '';
    let totalCorrect = 0, totalQuestions = 0;
    let anyDownloadable = false;

    allResults.forEach(result => {
        const secLabel = capitalize(result.section);
        html += '<div class="results-module">';
        html += '<h2 class="results-module__title">' + secLabel + ' — Module ' + result.moduleNum + '</h2>';

        if (result.score.total > 0) {
            const pct = Math.round((result.score.correct / result.score.total) * 100);
            totalCorrect += result.score.correct;
            totalQuestions += result.score.total;
            html += '<div class="results-score">';
            html += '<div class="results-score__circle"><span class="results-score__num">' + result.score.correct + '</span>';
            html += '<span class="results-score__den">/ ' + result.score.total + '</span></div>';
            html += '<div class="results-score__pct">' + pct + '%</div></div>';
        }

        html += '<div class="results-list">';
        result.details.forEach(d => {
            if (d.type === 'mc' || d.type === 'build_sentence') {
                const cls = d.correct ? 'result-row--correct' : 'result-row--wrong';
                const icon = d.correct ? 'OK' : 'X';
                html += '<div class="result-row ' + cls + '">';
                html += '<span class="result-row__q">Q' + d.qid + '</span>';
                html += '<span class="result-row__icon">' + icon + '</span>';
                html += '<span class="result-row__detail">Your answer: <strong>' + escapeHtml(String(d.user)) + '</strong>';
                if (!d.correct) html += ' | Correct: <strong>' + escapeHtml(d.expected) + '</strong>';
                html += '</span></div>';
            } else if (d.type === 'cloze') {
                const cls = d.correct ? 'result-row--correct' : 'result-row--wrong';
                const icon = d.correct ? 'OK' : 'X';
                html += '<div class="result-row ' + cls + '">';
                html += '<span class="result-row__q">' + d.qid + '</span>';
                html += '<span class="result-row__icon">' + icon + '</span>';
                html += '<span class="result-row__detail">';
                html += 'You wrote: <strong>' + escapeHtml(String(d.user)) + '</strong>';
                if (!d.correct) html += ' | Answer: <strong>' + escapeHtml(d.fullWord || d.expected) + '</strong>';
                else html += ' (' + escapeHtml(d.fullWord || '') + ')';
                html += '</span></div>';
            } else if (d.type === 'email' || d.type === 'discussion') {
                html += '<div class="result-row result-row--neutral">';
                html += '<span class="result-row__q">Q' + d.qid + '</span>';
                html += '<span class="result-row__icon">W</span>';
                html += '<span class="result-row__detail">' + capitalize(d.type) +
                         ' — ' + d.wordCount + ' words</span></div>';
            } else if (d.type === 'listen_repeat' || d.type === 'interview') {
                const cls = d.hasRecording ? 'result-row--neutral' : 'result-row--wrong';
                html += '<div class="result-row ' + cls + '">';
                html += '<span class="result-row__q">Q' + d.qid + '</span>';
                html += '<span class="result-row__icon">' + (d.hasRecording ? 'S' : '—') + '</span>';
                html += '<span class="result-row__detail">' + (d.hasRecording ? 'Audio recorded' : 'No recording') + '</span></div>';
            }
        });
        html += '</div></div>';

        if (result.hasDownloadable) anyDownloadable = true;
    });

    // Overall score bar for full test
    let overallHtml = '';
    if (isFull && totalQuestions > 0) {
        const pct = Math.round((totalCorrect / totalQuestions) * 100);
        overallHtml = '<div class="results-overall"><div class="results-score">' +
            '<div class="results-score__circle"><span class="results-score__num">' + totalCorrect + '</span>' +
            '<span class="results-score__den">/ ' + totalQuestions + '</span></div>' +
            '<div class="results-score__pct">Overall: ' + pct + '%</div></div></div>';
    }

    document.getElementById('results-body').innerHTML = overallHtml + html;

    // Actions
    const actionsEl = document.getElementById('results-actions');
    actionsEl.innerHTML = '';
    const backBtn = el('a', 'btn btn--primary', '← Back to Catalog');
    backBtn.href = '/';
    actionsEl.appendChild(backBtn);

    if (anyDownloadable) {
        const dlBtn = el('button', 'btn btn--secondary', 'Download Answers (.zip)');
        dlBtn.addEventListener('click', downloadFullZip);
        actionsEl.appendChild(dlBtn);
    }
}

/* ======= ZIP DOWNLOAD ======= */
async function downloadFullZip() {
    const zip = new JSZip();
    const testFolder = zip.folder(TEST_INFO.test_id + '_answers');

    allResults.forEach(result => {
        const folderName = result.section + '_M' + result.moduleNum;
        const folder = testFolder.folder(folderName);

        // Text answers
        let txt = 'Test: ' + TEST_INFO.test_name + '\n';
        txt += 'Section: ' + result.section + '\nModule: ' + result.moduleNum + '\n';
        txt += 'Date: ' + new Date().toISOString() + '\n';
        if (result.score.total > 0) {
            txt += 'Score: ' + result.score.correct + '/' + result.score.total + '\n';
        }
        txt += '\n';

        result.details.forEach(d => {
            txt += '--- Question ' + d.qid + ' (' + d.type + ') ---\n';
            if (d.type === 'email' || d.type === 'discussion') {
                txt += (d.user || '[no answer]') + '\n';
            } else if (d.type === 'listen_repeat' || d.type === 'interview') {
                txt += d.hasRecording ? '[see audio file]\n' : '[no recording]\n';
            } else {
                txt += 'Answer: ' + String(d.user) + '\n';
                if (d.correct !== undefined) txt += (d.correct ? 'CORRECT' : 'WRONG — expected: ' + d.expected) + '\n';
            }
            txt += '\n';
        });
        folder.file('answers.txt', txt);

        // Audio recordings
        if (result.recordings) {
            for (const [qid, blob] of Object.entries(result.recordings)) {
                if (blob instanceof Blob) {
                    folder.file('q' + qid + '_recording.' + recordingExt, blob);
                }
            }
        }
    });

    const content = await zip.generateAsync({ type: 'blob' });
    saveAs(content, TEST_INFO.test_id + '_answers.zip');
}

/* ======= KEYBOARD SHORTCUTS ======= */

// Warn before closing tab during active test
window.addEventListener('beforeunload', (e) => {
    if (currentModule) {
        e.preventDefault();
        e.returnValue = '';
    }
});

document.addEventListener('keydown', (e) => {
    // Ignore if typing in an input or textarea
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea') return;

    // Only act during test-taking on MC questions
    if (!currentModule) return;
    const page = currentModule.pages[currentPageIdx];
    if (!page || page.question_type !== 'mc') return;

    const key = e.key.toUpperCase();
    if (!['A', 'B', 'C', 'D'].includes(key)) return;

    const radio = document.querySelector('input[name="mc-answer"][value="' + key + '"]');
    if (radio) {
        radio.checked = true;
        radio.dispatchEvent(new Event('change', { bubbles: true }));
    }
});

/* ======= INIT ======= */
// (handled in DOMContentLoaded above)
