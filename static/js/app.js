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
let audioPlaying = false;   // True while audio is playing (blocks Next in listening)
let cachedMicStream = null; // Reuse mic stream across speaking questions
let bookmarkedQuestions = new Set(); // Bookmarked question indices (reading only)
let isPracticeMode = false;         // Practice mode: replay audio, instant feedback
let timerPaused = false;            // Timer pause state (practice mode only)


let questionTimes = {};             // { qid: seconds spent }
let questionStartTime = 0;          // Date.now() when current question was shown
let playedAudio = new Set();    // Track audio clips already played (for no-replay mode)

/* ======= AUDIO CODEC DETECTION ======= */
const AUDIO_TYPES = [
    { mimeType: 'audio/ogg;codecs=opus', ext: 'ogg' },
    { mimeType: 'audio/ogg', ext: 'ogg' },
    { mimeType: 'audio/webm;codecs=opus', ext: 'webm' },
    { mimeType: 'audio/webm', ext: 'webm' },
    { mimeType: 'audio/mp4', ext: 'm4a' },
];
let recordingMimeType = '';
let recordingExt = 'ogg';
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
// Server-side session (logged-in users only)
let serverSessionId = null;
let isLoggedIn = false;  // Set from template

function storageKey(suffix) {
    const mode = URL_PARAMS.mode || 'full';
    const practice = isPracticeMode ? 'prac_' : '';
    const scope = mode === 'section' ? 'sec_' + (URL_PARAMS.section || '') : 'full';
    return 'toefl_' + TEST_INFO.test_id + '_' + practice + scope + '_' + suffix;
}

function moduleKey(mod) {
    return mod.filename + '_' + mod.module_index;
}

function safeSetItem(key, value) {
    try {
        localStorage.setItem(key, value);
    } catch (e) {
        console.warn('localStorage write failed:', e);
    }
}

async function saveModuleProgress() {
    if (!currentModule) return;
    collectAnswer();
    if (isLoggedIn && serverSessionId) {
        // Server-side save
        try {
            await fetch('/api/session/' + serverSessionId + '/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    answers: answers,
                    current_page: currentPageIdx,
                    timer_left: timerSecondsLeft,
                    question_times: questionTimes,
                }),
            });
        } catch (e) {
            console.warn('Server save failed, using localStorage fallback:', e);
            _localSaveModule();
        }
    } else {
        _localSaveModule();
    }
}

function _localSaveModule() {
    if (!currentModule) return;
    const mod = playlist[playlistIdx];
    safeSetItem(storageKey('mod_' + moduleKey(mod)), JSON.stringify({
        pageIdx: currentPageIdx,
        answers: answers,
        timerSecondsLeft: timerSecondsLeft,
        questionTimes: questionTimes,
        savedAt: new Date().toISOString(),
    }));
}

function savePlaylistState() {
    if (isLoggedIn && serverSessionId) {
        // Server already tracks playlist state via advance endpoint
        return;
    }
    safeSetItem(storageKey('playlist'), JSON.stringify({
        playlist: playlist,
        playlistIdx: playlistIdx,
        allResults: allResults.map(r => ({ ...r, recordings: undefined })),
    }));
}

function loadModuleProgress(mod) {
    // For logged-in users, module state comes from session/start response
    // This is only used as a fallback for guests
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
    saveModuleProgress(); // Now async but fire-and-forget is fine for auto-save
}

function startAutoSave() {
    stopAutoSave();
    autoSaveInterval = setInterval(autoSave, 15000); // Save every 15s for server sessions
}

function stopAutoSave() {
    if (autoSaveInterval) clearInterval(autoSaveInterval);
    autoSaveInterval = null;
}

// Upload recordings for current module to server
async function uploadModuleRecordings() {
    if (!isLoggedIn || !serverSessionId) return;
    const recEntries = Object.entries(recordings).filter(([_, v]) => v instanceof Blob);
    if (recEntries.length === 0) return;
    const formData = new FormData();
    formData.append('_csrf', typeof CSRF_TOKEN !== 'undefined' ? CSRF_TOKEN : '');
    for (const [qid, blob] of recEntries) {
        formData.append('rec_' + qid, blob, 'q' + qid + '.' + recordingExt);
    }
    try {
        // Upload to a temporary session recording store
        await fetch('/api/session/' + serverSessionId + '/upload-recording', {
            method: 'POST',
            body: formData,
        });
    } catch (e) {
        console.warn('Recording upload failed:', e);
    }
}

/* ======= SCREEN SWITCHING ======= */
let _activeScreen = null;

function showScreen(id) {
    if (_activeScreen) _activeScreen.classList.remove('screen--active');
    _activeScreen = document.getElementById(id);
    _activeScreen.classList.add('screen--active');
}

/* ======= INITIALIZATION ======= */
document.addEventListener('DOMContentLoaded', async () => {
    _activeScreen = document.querySelector('.screen--active');
    const mode = URL_PARAMS.mode || 'full';
    isPracticeMode = URL_PARAMS.practice === 'true';
    isLoggedIn = typeof IS_LOGGED_IN !== 'undefined' && IS_LOGGED_IN;

    // Force English during test-taking
    window._lang = 'en';

    if (mode === 'section') {
        const sectionName = URL_PARAMS.section;
        const sectionMods = TEST_INFO.modules.filter(m => m.section === sectionName);
        if (!sectionMods.length) {
            alert('Section not found.');
            window.location.href = '/';
            return;
        }
        playlist = sectionMods;
    } else {
        playlist = [...TEST_INFO.modules];
        if (playlist.length === 0) {
            alert('No modules found in this test.');
            window.location.href = '/';
            return;
        }
    }

    playlistIdx = 0;
    allResults = [];

    // Server-side session for logged-in users
    if (isLoggedIn) {
        // Single shared request shape — was duplicated three times in the original flow.
        const sessionStartBody = {
            test_id: TEST_INFO.test_id,
            mode: mode,
            section: URL_PARAMS.section || null,
            practice: isPracticeMode,
            playlist: playlist,
        };
        async function startServerSession() {
            const r = await fetch('/api/session/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(sessionStartBody),
            });
            return r.json();
        }
        async function discardAndStartFresh(oldSid) {
            await fetch('/api/session/' + oldSid, { method: 'DELETE' });
            return startServerSession();
        }
        try {
            let sess = await startServerSession();
            serverSessionId = sess.session_id;
            if (sess.resumed && sess.playlist_idx !== undefined) {
                const resumeIdx = sess.playlist_idx;
                if (resumeIdx > 0 && resumeIdx < playlist.length) {
                    const wantResume = await confirmAsync(t('continue_'), t('resumeConfirm'), t('continue_'), { destructive: false });
                    if (wantResume) {
                        playlistIdx = resumeIdx;
                        allResults = Array.isArray(sess.completed) ? sess.completed : [];
                        _serverModuleState = sess;
                    } else {
                        // User declined resume — delete old session and create new
                        sess = await discardAndStartFresh(serverSessionId);
                        serverSessionId = sess.session_id;
                        _serverModuleState = null;
                    }
                } else if (resumeIdx >= playlist.length) {
                    // Previous session was completed — clean up and start fresh
                    sess = await discardAndStartFresh(serverSessionId);
                    serverSessionId = sess.session_id;
                    _serverModuleState = null;
                } else {
                    // playlist_idx is 0, module-level resume
                    _serverModuleState = sess;
                }
            } else {
                _serverModuleState = null;
            }
        } catch (e) {
            console.warn('Server session failed, falling back to localStorage:', e);
            _serverModuleState = null;
        }
    } else {
        // Guest: use localStorage
        _serverModuleState = null;
        if (mode === 'full') {
            const saved = loadPlaylistProgress();
            if (saved && saved.playlistIdx > 0 && saved.playlistIdx < playlist.length) {
                const wantResume = await confirmAsync(t('continue_'), t('resumeConfirm'), t('continue_'), { destructive: false });
                if (wantResume) {
                    playlistIdx = saved.playlistIdx;
                    allResults = Array.isArray(saved.allResults) ? saved.allResults : [];
                }
            } else if (saved && saved.playlistIdx >= playlist.length) {
                localStorage.removeItem(storageKey('playlist'));
            }
        }
    }

    loadAndStartModule();
});

// Temporary holder for server module state during init
let _serverModuleState = null;

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
        const url = '/api/module/' + encodeURIComponent(mod.filename) + '?module_index=' + mod.module_index + (isPracticeMode ? '&practice=true' : '');
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

        // Restore progress from server session or localStorage
        let saved = null;
        if (isLoggedIn && _serverModuleState && _serverModuleState.answers && Object.keys(_serverModuleState.answers).length > 0) {
            saved = {
                answers: _serverModuleState.answers,
                pageIdx: _serverModuleState.current_page,
                timerSecondsLeft: _serverModuleState.timer_left,
                questionTimes: _serverModuleState.question_times || {},
            };
            _serverModuleState = null; // Consume it
        } else if (!isLoggedIn) {
            saved = loadModuleProgress(mod);
        }
        if (saved && saved.answers && Object.keys(saved.answers).length > 0) {
            answers = saved.answers;
            currentPageIdx = saved.pageIdx ?? 0;
            questionTimes = saved.questionTimes || {};
            const restoredTime = saved.timerSecondsLeft;
            timerSecondsLeft = (restoredTime != null && restoredTime > 0) ? restoredTime : (mod.timer_minutes * 60);
        } else {
            answers = {};
            currentPageIdx = 0;
            timerSecondsLeft = mod.timer_minutes * 60;
            questionTimes = {};
        }
        recordings = {};
        playedAudio = new Set();
        isFinishing = false;
        audioPlaying = false;
        bookmarkedQuestions = new Set();
        questionStartTime = 0;
        // Release mic if switching away from speaking
        if (cachedMicStream && currentModule.section !== 'speaking') {
            cachedMicStream.getTracks().forEach(t => t.stop());
            cachedMicStream = null;
        }

        // UI — just the section name centered
        document.getElementById('section-label').textContent = capitalize(currentModule.section);

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
    const confirmed = await confirmAsync(t('finish'), t('exitConfirm'), t('finish'), { destructive: true });
    if (!confirmed) return;
    collectAnswer();
    saveModuleProgress();
    stopTimer();
    stopAutoSave();
    await stopRecording();
    if (cachedMicStream) {
        cachedMicStream.getTracks().forEach(t => t.stop());
        cachedMicStream = null;
    }
    window.location.href = '/';
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
        if (timerPaused) return;  // Skip tick when paused
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
    timerPaused = false;
}

function togglePause() {
    if (!isPracticeMode) return;
    timerPaused = !timerPaused;
    const btn = document.getElementById('btn-pause');
    const icon = document.getElementById('pause-icon');
    if (timerPaused) {
        icon.innerHTML = '<polygon points="6,4 17,10 6,16" fill="currentColor"/>';
        btn.title = 'Resume timer';
        document.getElementById('timer').style.opacity = '0.5';
    } else {
        icon.innerHTML = '<rect x="5" y="4" width="3.5" height="12" rx="1" fill="currentColor"/><rect x="11.5" y="4" width="3.5" height="12" rx="1" fill="currentColor"/>';
        btn.title = 'Pause timer';
        document.getElementById('timer').style.opacity = '1';
    }
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

    // Reset audio state for new question
    audioPlaying = false;
    setNextButtonEnabled(true);

    // Prev button: only reading
    const btnPrev = document.getElementById('btn-prev');
    btnPrev.style.display = (section === 'reading' && currentPageIdx > 0) ? '' : 'none';

    // Bookmark button: only reading
    const btnBookmark = document.getElementById('btn-bookmark');
    if (btnBookmark) {
        btnBookmark.style.display = (section === 'reading') ? '' : 'none';
        btnBookmark.classList.toggle('btn-bookmark--active', bookmarkedQuestions.has(currentPageIdx));
    }

    // Pause button: practice mode only
    const btnPause = document.getElementById('btn-pause');
    if (btnPause) {
        btnPause.style.display = isPracticeMode ? '' : 'none';
    }

    // Next button text
    const btnNext = document.getElementById('btn-next');
    if (currentPageIdx === total - 1) {
        if (playlistIdx < playlist.length - 1) {
            btnNext.textContent = t('nextSection');
        } else {
            btnNext.textContent = t('finish');
        }
    } else {
        btnNext.textContent = t('next');
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

    updateProgressDots();
    questionStartTime = Date.now();
}

function recordQuestionTime() {
    if (!currentModule || !questionStartTime) return;
    const qid = currentModule.pages[currentPageIdx].question_id;
    const elapsed = Math.round((Date.now() - questionStartTime) / 1000);
    questionTimes[qid] = (questionTimes[qid] || 0) + elapsed;
    questionStartTime = 0;
}

let _navigating = false;

async function nextQuestion() {
    if (_navigating || audioPlaying) return;
    _navigating = true;
    try {
        recordQuestionTime();
        collectAnswer();
        saveModuleProgress();
        await stopRecording();
        stopQuestionTimer();

        if (currentPageIdx >= currentModule.pages.length - 1) {
            // Confirm if student still has time left
            if (timerSecondsLeft > 0) {
                const mins = Math.ceil(timerSecondsLeft / 60);
                const section = capitalize(currentModule.section);
                const canGoBack = currentModule.section === 'reading';
                let msg = t('timeLeftConfirm').replace('{n}', mins).replace('{section}', section);
                if (canGoBack) msg += ' ' + t('canReview');
                msg += '\n\n' + t('finishConfirm');
                const confirmed = await confirmAsync(t('finish'), msg, t('finish'), { destructive: false });
                if (!confirmed) return;
            }
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
    recordQuestionTime();
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

function updateProgressDots() {
    const container = document.getElementById('progress-dots');
    const counter = document.getElementById('progress-counter');
    if (!container || !currentModule) return;
    const total = currentModule.pages.length;

    // Update counter
    if (counter) counter.textContent = (currentPageIdx + 1) + ' / ' + total;

    // Build dots if needed
    if (container.children.length !== total) {
        container.innerHTML = '';
        for (let i = 0; i < total; i++) {
            const dot = document.createElement('span');
            dot.className = 'progress-dot';
            dot.setAttribute('aria-label', 'Question ' + (i + 1));
            container.appendChild(dot);
        }
    }

    // Update state
    for (let i = 0; i < total; i++) {
        const dot = container.children[i];
        const qid = currentModule.pages[i].question_id;
        const isAnswered = answers[qid] !== undefined && answers[qid] !== '' &&
            !(Array.isArray(answers[qid]) && answers[qid].every(v => v === ''));
        dot.classList.toggle('progress-dot--answered', isAnswered);
        dot.classList.toggle('progress-dot--current', i === currentPageIdx);
        dot.classList.toggle('progress-dot--bookmarked', bookmarkedQuestions.has(i));
    }
}

function toggleBookmark() {
    if (bookmarkedQuestions.has(currentPageIdx)) {
        bookmarkedQuestions.delete(currentPageIdx);
    } else {
        bookmarkedQuestions.add(currentPageIdx);
    }
    const btn = document.getElementById('btn-bookmark');
    if (btn) btn.classList.toggle('btn-bookmark--active', bookmarkedQuestions.has(currentPageIdx));
    updateProgressDots();
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
    const hasPassage = !!page.passage_html;

    if (hasPassage) {
        // Split-pane: passage left, question right
        body.classList.add('test-body--splitpane');
        const pane = el('div', 'split-pane');

        const left = el('div', 'split-pane__left');
        const passageEl = el('div', 'passage-panel passage-panel--full');
        passageEl.innerHTML = page.passage_html;
        left.appendChild(passageEl);
        if (page.audio) {
            const isListening = page.section === 'listening';
            left.appendChild(createAudioPlayer(page.audio, {
                lockNext: isListening,
            }));
        }
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

        const existingLabel = body.querySelector('.group-label');
        if (existingLabel) existingLabel.remove();

        body.appendChild(pane);
    } else {
        body.classList.remove('test-body--splitpane');
        const choicesEl = buildChoices(page);
        if (page.audio) {
            const isListening = page.section === 'listening';
            body.appendChild(createAudioPlayer(page.audio, {
                lockNext: isListening,
                lockEl: isListening ? choicesEl : null,
            }));
        }
        if (page.prompt_html) {
            const p = el('div', 'question-prompt');
            p.innerHTML = page.prompt_html;
            body.appendChild(p);
        } else if (page.prompt) {
            body.appendChild(el('div', 'question-prompt', escapeHtml(page.prompt)));
        }
        body.appendChild(choicesEl);
    }
}

function buildChoices(page) {
    const choicesEl = el('div', 'choices');
    choicesEl.setAttribute('role', 'radiogroup');
    choicesEl.setAttribute('aria-label', 'Answer choices');
    const saved = answers[page.question_id] || '';
    for (const [letter, text] of Object.entries(page.choices)) {
        const label = document.createElement('label');
        label.className = 'choice' + (saved === letter ? ' choice--selected' : '');
        label.setAttribute('data-letter', letter);
        const radio = document.createElement('input');
        radio.type = 'radio'; radio.name = 'mc-answer'; radio.value = letter;
        radio.setAttribute('aria-label', 'Option ' + letter + ': ' + text);
        if (saved === letter) radio.checked = true;
        radio.addEventListener('change', () => {
            choicesEl.querySelectorAll('.choice--selected').forEach(c => c.classList.remove('choice--selected'));
            label.classList.add('choice--selected');
            // Practice mode: instant feedback
            if (isPracticeMode && page.answer) {
                choicesEl.querySelectorAll('.choice').forEach(c => {
                    c.classList.remove('choice--correct', 'choice--wrong');
                    const l = c.getAttribute('data-letter');
                    if (l === page.answer) c.classList.add('choice--correct');
                    else if (l === letter) c.classList.add('choice--wrong');
                    // Disable further changes
                    c.querySelector('input').disabled = true;
                    c.style.pointerEvents = 'none';
                });
            }
        });
        label.appendChild(radio);
        label.appendChild(el('span', 'choice__text', escapeHtml(text)));
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
                'value="' + escapeHtml(charVal) + '" autocomplete="off" autocapitalize="off" spellcheck="false" ' +
                'aria-label="Blank ' + (idx + 1) + ', letter ' + (c + 1) + ' of ' + count + '">';
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
    body.appendChild(el('div', 'cloze-hint', t('fillBlanks').replace('{n}', idx)));

    // Wire up auto-advance behavior
    const allChars = Array.from(p.querySelectorAll('.cloze-char'));
    allChars.forEach((inp, i) => {
        inp.addEventListener('input', () => {
            if (inp.value.length === 1) {
                // Advance to the next input, but stop at the last one — don't wrap back to the
                // first, which is disorienting when the student has just finished the last blank.
                const next = allChars[i + 1];
                if (next) { next.focus(); next.select(); }
                else inp.blur();
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
    const hasContext = !!(details.context_html || details.context);

    // Build the right-side content (slots + word bank)
    function buildRightContent(container) {
        container.appendChild(el('div', 'bs-instruction', t('tapWords')));

        const slotsArea = el('div', 'sentence-slots'); slotsArea.id = 'sentence-slots';
        slotsArea.setAttribute('data-empty', 'true');
        slotsArea.setAttribute('data-placeholder', t('tapWords').split('.')[0] + '.');
        const lastChar = (page.answer || '').trim().slice(-1);
        const punct = /[?!.]/.test(lastChar) ? lastChar : '.';
        const punctEl = el('span', 'sentence-punct', punct);
        punctEl.id = 'sentence-punct';
        slotsArea.appendChild(punctEl);
        container.appendChild(slotsArea);

        function updateCapitalization() {
            const placed = slotsArea.querySelectorAll('.word-chip--placed');
            slotsArea.setAttribute('data-empty', placed.length === 0 ? 'true' : 'false');
            placed.forEach((chip, i) => {
                const orig = chip.getAttribute('data-word');
                chip.textContent = i === 0 ? capitalize(orig) : orig;
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
                slotsArea.insertBefore(placed, punctEl);
                updateCapitalization();
            });
            bank.appendChild(chip);
        });
        container.appendChild(bank);

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

    if (hasContext) {
        body.classList.add('test-body--splitpane');
        const pane = el('div', 'split-pane');
        const left = el('div', 'split-pane__left');
        const contextEl = el('div', 'passage-panel passage-panel--full');
        if (details.context_html) contextEl.innerHTML = details.context_html;
        else contextEl.innerHTML = '<strong>Context:</strong> ' + escapeHtml(details.context);
        left.appendChild(contextEl);
        pane.appendChild(left);

        const right = el('div', 'split-pane__right');
        right.appendChild(el('div', 'group-label', page.group_title));
        buildRightContent(right);
        pane.appendChild(right);

        const existingLabel = body.querySelector('.group-label');
        if (existingLabel) existingLabel.remove();
        body.appendChild(pane);
    } else {
        buildRightContent(body);
    }
}

function renderFreeWrite(body, page) {
    const hasContent = !!page.content_html;
    const ta = document.createElement('textarea');
    ta.id = 'free-write-area'; ta.className = 'free-write-area';
    ta.placeholder = t('typeResponse'); ta.rows = 14;
    ta.value = answers[page.question_id] || '';
    const wc = el('div', 'word-count', '0 ' + t('words'));
    ta.addEventListener('input', () => {
        const count = ta.value.trim() ? ta.value.trim().split(/\s+/).length : 0;
        wc.textContent = count + ' ' + t('words');
    });

    if (hasContent) {
        // Split-pane: prompt left, writing area right
        body.classList.add('test-body--splitpane');
        const pane = el('div', 'split-pane');
        const left = el('div', 'split-pane__left');
        const prompt = el('div', 'passage-panel passage-panel--full');
        prompt.innerHTML = page.content_html;
        left.appendChild(prompt);
        pane.appendChild(left);

        const right = el('div', 'split-pane__right');
        right.appendChild(el('div', 'group-label', page.group_title));
        if (page.time_minutes) {
            right.appendChild(el('div', 'write-time-hint', t('suggestedTime').replace('{n}', page.time_minutes)));
        }
        right.appendChild(ta);
        right.appendChild(wc);
        pane.appendChild(right);

        const existingLabel = body.querySelector('.group-label');
        if (existingLabel) existingLabel.remove();
        body.appendChild(pane);
    } else {
        if (page.time_minutes) {
            body.appendChild(el('div', 'write-time-hint', t('suggestedTime').replace('{n}', page.time_minutes)));
        }
        body.appendChild(ta);
        body.appendChild(wc);
    }
    ta.dispatchEvent(new Event('input'));
}

function renderSpeaking(body, page) {
    const hasContent = !!(page.content_html || (page.content && page.content !== t('listenRespond')));

    // Build recording controls
    function buildControls(container) {
        container.appendChild(el('div', 'speak-instruction', page.content && !hasContent ? page.content : t('listenRespond')));
        const statusArea = el('div', 'speak-status'); statusArea.id = 'speak-status';
        const meterWrap = el('div', 'level-meter');
        meterWrap.style.display = 'none';
        meterWrap.setAttribute('aria-label', 'Microphone input level');
        const meterFill = el('div', 'level-meter__fill');
        meterWrap.appendChild(meterFill);
        const timerWrap = el('div', 'q-timer-wrap');
        timerWrap.innerHTML = '<div class="q-timer-track"><div class="q-timer-bar" id="q-timer-bar"></div></div>' +
            '<span class="q-timer-label" id="q-timer-label">--</span>';
        container.appendChild(statusArea);
        container.appendChild(meterWrap);
        container.appendChild(timerWrap);
        return { statusArea, meterWrap, meterFill };
    }

    let statusArea, meterWrap, meterFill;

    if (hasContent) {
        body.classList.add('test-body--splitpane');
        const pane = el('div', 'split-pane');
        const left = el('div', 'split-pane__left');
        const promptEl = el('div', 'passage-panel passage-panel--full');
        if (page.content_html) promptEl.innerHTML = page.content_html;
        else promptEl.innerHTML = escapeHtml(page.content);
        left.appendChild(promptEl);
        pane.appendChild(left);

        const right = el('div', 'split-pane__right');
        right.appendChild(el('div', 'group-label', page.group_title));
        const ctrl = buildControls(right);
        statusArea = ctrl.statusArea; meterWrap = ctrl.meterWrap; meterFill = ctrl.meterFill;
        pane.appendChild(right);

        const existingLabel = body.querySelector('.group-label');
        if (existingLabel) existingLabel.remove();
        body.appendChild(pane);
    } else {
        const ctrl = buildControls(body);
        statusArea = ctrl.statusArea; meterWrap = ctrl.meterWrap; meterFill = ctrl.meterFill;
    }

    let meterAnimId = null;
    let analyser = null;
    let meterAudioCtx = null;

    function startMeter(stream) {
        try {
            meterAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
            const source = meterAudioCtx.createMediaStreamSource(stream);
            analyser = meterAudioCtx.createAnalyser();
            analyser.fftSize = 256;
            source.connect(analyser);
            meterWrap.style.display = '';
            const data = new Uint8Array(analyser.fftSize);
            function draw() {
                meterAnimId = requestAnimationFrame(draw);
                analyser.getByteTimeDomainData(data);
                // Compute RMS volume
                let sum = 0;
                for (let i = 0; i < data.length; i++) {
                    const v = (data[i] - 128) / 128;
                    sum += v * v;
                }
                const rms = Math.sqrt(sum / data.length);
                const pct = Math.min(100, rms * 400); // Scale for visibility
                meterFill.style.width = pct + '%';
                // Color: green when loud, accent when quiet
                if (pct > 50) {
                    meterFill.classList.add('level-meter__fill--active');
                } else {
                    meterFill.classList.remove('level-meter__fill--active');
                }
            }
            draw();
        } catch (e) {
            // Web Audio API not available
        }
    }

    function stopMeter() {
        if (meterAnimId) {
            cancelAnimationFrame(meterAnimId);
            meterAnimId = null;
        }
        analyser = null;
        if (meterAudioCtx) {
            meterAudioCtx.close().catch(() => {});
            meterAudioCtx = null;
        }
        meterFill.style.width = '0%';
    }

    // Hide Next button — speaking auto-advances
    setNextButtonEnabled(false);

    async function getMicStream() {
        if (cachedMicStream) {
            const tracks = cachedMicStream.getTracks();
            if (tracks.length > 0 && tracks[0].readyState === 'live') {
                return cachedMicStream;
            }
        }
        // Request mono audio optimized for speech — reduces bandwidth ~80%
        cachedMicStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                channelCount: { ideal: 1 },
                sampleRate: { ideal: 16000 },
                echoCancellation: true,
                noiseSuppression: true,
            }
        });
        return cachedMicStream;
    }

    async function startAutoRecord() {
        statusArea.textContent = t('recordIn3');
        await sleep(1000);
        if (!currentModule) return;
        statusArea.textContent = t('recordIn2');
        await sleep(1000);
        if (!currentModule) return;
        statusArea.textContent = t('recordIn1');
        await sleep(1000);
        if (!currentModule) return;

        if (!recordingMimeType) {
            statusArea.textContent = t('recNotSupported');
            setNextButtonEnabled(true);
            return;
        }

        try {
            const stream = await getMicStream();
            mediaRecorder = new MediaRecorder(stream, {
                mimeType: recordingMimeType,
                audioBitsPerSecond: 32000,  // 32kbps Opus — excellent for speech, ~5x smaller than default
            });
            audioChunks = [];
            mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
            mediaRecorder.onstop = () => {
                const blob = new Blob(audioChunks, { type: recordingMimeType });
                recordings[page.question_id] = blob;
                answers[page.question_id] = '[audio recorded]';
                isRecording = false;
                stopMeter();
                statusArea.textContent = t('recordSaved');
                if (currentModule && !isFinishing) nextQuestion();
            };
            mediaRecorder.start();
            isRecording = true;
            statusArea.textContent = t('recording');
            startMeter(stream);
            const duration = page.time_seconds ?? 30;
            startQuestionTimer(duration, () => {
                if (isRecording) {
                    stopMeter();
                    stopRecordingAndSave();
                }
            });
        } catch (err) {
            statusArea.textContent = t('micDenied');
            setNextButtonEnabled(true);
        }
    }

    if (page.audio) {
        const audioPlayer = createAudioPlayer(page.audio, {
            onEnded: () => startAutoRecord(),
        });
        if (hasContent) {
            // In split-pane, put audio at top of right panel (before status area)
            const right = body.querySelector('.split-pane__right');
            if (right && statusArea.parentNode === right) {
                right.insertBefore(audioPlayer, statusArea);
            } else {
                body.insertBefore(audioPlayer, body.firstChild);
            }
        } else {
            body.insertBefore(audioPlayer, statusArea);
        }
    } else {
        startAutoRecord();
    }
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

let _pendingStop = null; // Shared Promise for in-progress recording stop

// Internal: kicks off MediaRecorder.stop() and returns a Promise that resolves when the
// onstop handler has fired (preserving whatever handler startAutoRecord assigned for
// saving the blob). Re-entrant: subsequent callers await the same Promise.
function _beginStop() {
    if (_pendingStop) return _pendingStop;
    if (!mediaRecorder || mediaRecorder.state === 'inactive') {
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

function stopRecordingAndSave() {
    stopQuestionTimer();
    _beginStop();
}

function stopRecording() {
    stopQuestionTimer();
    return _beginStop();
}

function setNextButtonEnabled(enabled) {
    const btn = document.getElementById('btn-next');
    if (!btn) return;
    btn.disabled = !enabled;
    btn.classList.toggle('btn--disabled', !enabled);
}

function createAudioPlayer(src, options) {
    const opts = options || {};
    const onEnded = opts.onEnded;
    const lockNext = isPracticeMode ? false : (opts.lockNext || false);
    const lockEl = isPracticeMode ? null : (opts.lockEl || null);
    const wrap = el('div', 'audio-player');
    const note = el('div', 'audio-note', '');
    wrap.appendChild(note);

    // Practice mode: normal replayable audio
    if (isPracticeMode) {
        const audioEl = document.createElement('audio');
        audioEl.preload = 'auto';
        audioEl.src = '/audio/' + encodeURIComponent(currentModule.audioDir) + '/' + encodeURIComponent(src) + '.ogg';
        audioEl.controls = true;
        note.textContent = src + '.ogg';
        wrap.appendChild(audioEl);
        audioEl.addEventListener('ended', () => { if (onEnded) onEnded(); });
        audioEl.addEventListener('error', () => {
            note.innerHTML = t('audioNotFound') + ': <code>' + src + '.ogg</code>';
            note.classList.add('audio-note--error');
            if (onEnded) onEnded();
        });
        return wrap;
    }

    // Test mode: already played — show disabled state
    if (playedAudio.has(src)) {
        note.textContent = t('audioAlreadyPlayed');
        note.className = 'audio-note audio-note--played';
        if (onEnded) onEnded();
        return wrap;
    }

    const audioEl = document.createElement('audio');
    audioEl.preload = 'auto';
    audioEl.src = '/audio/' + encodeURIComponent(currentModule.audioDir) + '/' + encodeURIComponent(src) + '.ogg';
    audioEl.controls = false;
    wrap.appendChild(audioEl);

    // Progress bar
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
        note.textContent = t('audioPlayed');
        note.className = 'audio-note audio-note--played';
        audioEl.remove();
        progressWrap.remove();
        audioPlaying = false;
        if (lockNext) setNextButtonEnabled(true);
        if (lockEl) lockEl.classList.remove('choices--locked');
        if (onEnded) onEnded();
    });

    audioEl.addEventListener('error', () => {
        note.innerHTML = 'Audio file not found: <code>' + src + '.ogg</code>';
        note.classList.add('audio-note--error');
        audioPlaying = false;
        if (lockNext) setNextButtonEnabled(true);
        if (lockEl) lockEl.classList.remove('choices--locked');
        if (onEnded) onEnded();
    });

    // Lock Next button and choices during playback
    if (lockNext) {
        audioPlaying = true;
        setNextButtonEnabled(false);
    }
    if (lockEl) lockEl.classList.add('choices--locked');

    note.textContent = t('loadingAudio');

    const volumeHtml = '<span class="audio-volume"><span class="audio-volume__bar"></span><span class="audio-volume__bar"></span><span class="audio-volume__bar"></span></span>';

    // Wait for enough data to play smoothly, then start
    let _playbackStarted = false;
    function startPlayback() {
        if (_playbackStarted) return;
        _playbackStarted = true;
        note.innerHTML = t('playing') + volumeHtml;
        audioPlaying = true;
        playedAudio.add(src);
        const playPromise = audioEl.play();
        if (playPromise !== undefined) {
            playPromise.catch(() => {
                // Browser blocked autoplay — unlock everything, show manual button
                audioPlaying = false;
                if (lockNext) setNextButtonEnabled(true);
                if (lockEl) lockEl.classList.remove('choices--locked');
                note.textContent = '';
                progressWrap.style.display = 'none';
                const playBtn = el('button', 'btn btn--primary audio-play-btn', 'Click to Play Audio');
                playBtn.addEventListener('click', () => {
                    audioPlaying = true;
                    if (lockNext) setNextButtonEnabled(false);
                    if (lockEl) lockEl.classList.add('choices--locked');
                    audioEl.play();
                    playBtn.remove();
                    note.innerHTML = t('playing') + volumeHtml;
                    progressWrap.style.display = '';
                }, { once: true });
                note.appendChild(playBtn);
            });
        }
    }

    // Use canplaythrough to avoid glitchy playback from insufficient buffering
    if (audioEl.readyState >= 4) {
        startPlayback();
    } else {
        audioEl.addEventListener('canplaythrough', startPlayback, { once: true });
        // Fallback: if canplaythrough never fires (e.g. very slow network), try after 3s
        setTimeout(() => {
            if (!playedAudio.has(src)) startPlayback();
        }, 3000);
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

    // Record time for current question before grading
    recordQuestionTime();

    // Upload recordings immediately (per-module, not at the end)
    await uploadModuleRecordings();

    // Server-side grading (no client-side fallback for security)
    const mod = playlist[playlistIdx];
    let result;
    try {
        const gradeResp = await fetch('/api/grade', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                filename: mod.filename,
                module_index: mod.module_index,
                answers: answers,
                times: questionTimes,
            }),
        });
        if (!gradeResp.ok) throw new Error('Grading failed: ' + gradeResp.status);
        result = await gradeResp.json();
    } catch (e) {
        alert('Failed to submit answers. Please check your connection and try again.');
        isFinishing = false;
        startAutoSave();
        return;
    }
    // Attach recordings for zip download
    result.recordings = { ...recordings };
    result.answers = { ...answers };
    allResults.push(result);

    // Mark complete, clear per-module progress
    markModuleComplete(mod);
    localStorage.removeItem(storageKey('mod_' + moduleKey(mod)));

    // Clear current module so stale data isn't saved
    currentModule = null;

    // Advance playlist
    playlistIdx++;

    // Advance server session
    if (isLoggedIn && serverSessionId) {
        try {
            const advResp = await fetch('/api/session/' + serverSessionId + '/advance', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    playlist_idx: playlistIdx,
                    graded_result: {
                        section: result.section,
                        moduleNum: result.moduleNum,
                        score: result.score,
                        details: result.details,
                    },
                }),
            });
            const advData = await advResp.json();
            // Update _serverModuleState with new timer for next module
            if (playlistIdx < playlist.length) {
                _serverModuleState = {
                    answers: {},
                    current_page: 0,
                    timer_left: advData.timer_left || 0,
                    question_times: {},
                };
            }
        } catch (e) {
            console.warn('Session advance failed:', e);
        }
    }

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
            document.getElementById('transition-done').textContent = doneSec + ' ' + t('complete');
            document.getElementById('transition-section').textContent = nextSec;
            document.getElementById('transition-badge').textContent = nextMod.timer_minutes + ' min';
            showScreen('screen-transition');
        }
    } else {
        localStorage.removeItem(storageKey('playlist'));
        showFinalResults();
    }
}

/* ======= FINAL RESULTS SCREEN ======= */
async function showFinalResults() {
    showScreen('screen-results');

    const isFull = playlist.length > 1;
    let titleText = isFull ? t('fullTestResults') : t('results');
    if (isPracticeMode) titleText = t('practiceResults');
    document.getElementById('results-title').textContent = titleText;

    let html = '';
    let totalCorrect = 0, totalQuestions = 0;

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
            const checkSvg = '<svg width="18" height="18" viewBox="0 0 18 18"><path d="M4 9l3.5 3.5L14 5" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
            const crossSvg = '<svg width="18" height="18" viewBox="0 0 18 18"><path d="M5 5l8 8M13 5l-8 8" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>';
            const timeLabel = d.time ? '<span class="result-row__time">' + d.time + 's</span>' : '';

            if (d.type === 'mc' || d.type === 'build_sentence') {
                const cls = d.correct ? 'result-row--correct' : 'result-row--wrong';
                html += '<div class="result-row ' + cls + '">';
                html += '<span class="result-row__q">Q' + d.qid + '</span>';
                html += '<span class="result-row__icon">' + (d.correct ? checkSvg : crossSvg) + '</span>';
                html += '<span class="result-row__ans">' + escapeHtml(String(d.user)) + '</span>';
                html += '<span class="result-row__correct">' + escapeHtml(d.expected) + '</span>';
                html += timeLabel + '</div>';
            } else if (d.type === 'cloze') {
                const cls = d.correct ? 'result-row--correct' : 'result-row--wrong';
                html += '<div class="result-row ' + cls + '">';
                html += '<span class="result-row__q">' + d.qid + '</span>';
                html += '<span class="result-row__icon">' + (d.correct ? checkSvg : crossSvg) + '</span>';
                html += '<span class="result-row__ans">' + escapeHtml(String(d.user)) + '</span>';
                html += '<span class="result-row__correct">' + escapeHtml(d.fullWord || d.expected) + '</span>';
                html += timeLabel + '</div>';
            } else if (d.type === 'email' || d.type === 'discussion') {
                const pencilSvg = '<svg width="18" height="18" viewBox="0 0 18 18"><path d="M11.5 3.5l3 3L6 15H3v-3L11.5 3.5z" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linejoin="round"/></svg>';
                html += '<div class="result-row result-row--neutral">';
                html += '<span class="result-row__q">Q' + d.qid + '</span>';
                html += '<span class="result-row__icon">' + pencilSvg + '</span>';
                html += '<span class="result-row__detail">' + capitalize(d.type) +
                         ' — ' + d.wordCount + ' words</span>' + timeLabel + '</div>';
            } else if (d.type === 'listen_repeat' || d.type === 'interview') {
                const micSvg = '<svg width="18" height="18" viewBox="0 0 18 18"><rect x="6" y="2" width="6" height="9" rx="3" stroke="currentColor" stroke-width="1.5" fill="none"/><path d="M4 9a5 5 0 0010 0M9 14v2" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round"/></svg>';
                const noMicSvg = '<svg width="18" height="18" viewBox="0 0 18 18"><path d="M3 3l12 12" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round"/><rect x="6" y="2" width="6" height="9" rx="3" stroke="currentColor" stroke-width="1.5" fill="none" opacity="0.4"/></svg>';
                const cls = d.hasRecording ? 'result-row--neutral' : 'result-row--wrong';
                html += '<div class="result-row ' + cls + '">';
                html += '<span class="result-row__q">Q' + d.qid + '</span>';
                html += '<span class="result-row__icon">' + (d.hasRecording ? micSvg : noMicSvg) + '</span>';
                html += '<span class="result-row__detail">' + (d.hasRecording ? 'Audio recorded' : 'No recording') + '</span>' + timeLabel + '</div>';
            }
        });
        html += '</div></div>';

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

    let practiceBadge = '';
    if (isPracticeMode) {
        practiceBadge = '<div style="text-align:center;margin-bottom:20px"><span class="practice-badge">' + t('practiceLabel') + '</span></div>';
    }

    document.getElementById('results-body').innerHTML = practiceBadge + overallHtml + html;

    // Actions
    const actionsEl = document.getElementById('results-actions');
    actionsEl.innerHTML = '';
    const backBtn = el('a', 'btn btn--primary', t('backToCatalog'));
    backBtn.href = '/';
    actionsEl.appendChild(backBtn);

    // Save results to server (for logged-in users)
    try {
        const saveResp = await fetch('/api/save-results', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                test_id: TEST_INFO.test_id,
                test_name: TEST_INFO.test_name,
                practice: isPracticeMode,
                total_correct: totalCorrect,
                total_questions: totalQuestions,
                session_id: serverSessionId || null,
                sections: allResults.map(r => ({
                    section: r.section, moduleNum: r.moduleNum,
                    score: r.score, details: r.details,
                })),
            }),
        });
        const saveData = await saveResp.json();
        // Move session recordings to result storage if we got a result_id
        if (saveData.ok && saveData.result_id && serverSessionId) {
            try {
                await fetch('/api/session/' + serverSessionId + '/finalize-recordings/' + saveData.result_id, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: '{}',
                });
            } catch (e) {}
        }
    } catch (e) {}
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
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea') return;
    if (!currentModule) return;

    // Arrow keys for navigation (all sections)
    if (e.key === 'ArrowRight' || e.key === 'Enter') {
        if (!audioPlaying && !e.repeat) {
            e.preventDefault();
            nextQuestion();
        }
        return;
    }
    if (e.key === 'ArrowLeft') {
        if (currentModule.section === 'reading' && currentPageIdx > 0) {
            e.preventDefault();
            prevQuestion();
        }
        return;
    }

    // A/B/C/D for multiple choice
    if (audioPlaying) return;
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
