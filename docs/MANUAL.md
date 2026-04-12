# 超能录 TOEFL Practice Test System — Technical Manual

This document explains how the system works internally: architecture, data flow, backend processing, frontend test engine, grading logic, progress saving, and the audio pipeline. For the markdown test format syntax, see `FORMAT.md`. For a project overview and quickstart, see `README.md`.


## 1. Architecture

The system is a Flask web application with a vanilla JavaScript frontend. There is no database — test content lives in markdown files, and student progress lives in browser localStorage.

```
┌─────────────────────────────────────────────────────────────┐
│  Browser (student)                                          │
│                                                             │
│  catalog.html          test.html + app.js                   │
│  ┌──────────┐          ┌─────────────────────────────────┐  │
│  │ Test     │  click   │ Loading → Test → Transition →   │  │
│  │ Catalog  │ ───────► │ Results  (one-question-per-page) │  │
│  │          │          │                                  │  │
│  │ dropdown │          │ Timer, audio, recording, grading │  │
│  └──────────┘          └──────────┬──────────────────────┘  │
│                                   │                         │
│                        localStorage (answers, timer, playlist)
└───────────────────────────────────┼─────────────────────────┘
                                    │ fetch
┌───────────────────────────────────┼─────────────────────────┐
│  Flask server (app.py)            │                         │
│                                   ▼                         │
│  GET /                    → catalog.html (Jinja2 template)  │
│  GET /test/<test_id>      → test.html   (Jinja2 template)  │
│  GET /api/tests           → JSON: all tests metadata        │
│  GET /api/module/<file>   → JSON: parsed pages with HTML    │
│  GET /audio/<path>        → .ogg audio file                 │
│                                   │                         │
│  Caching layer:                   │                         │
│  _cached_scan()    ← mtime+count  │                         │
│  _cached_parse()   ← mtime        │                         │
│  _cached_module_response() ← mtime │                        │
│                                   │                         │
│  parser.py                        │                         │
│  ┌────────────────────────────┐   │                         │
│  │ scan_test_headers()        │ lightweight (catalog only)  │
│  │ parse_test_file()          │ full parse (API requests)   │
│  │ build_question_list()      │ flatten to page list        │
│  └────────────────────────────┘                             │
│                                                             │
│  tests/                                                     │
│  ├── pt1-reading-m1.md     ← markdown source                │
│  ├── pt1-listening-m1.md                                    │
│  ├── pt1-listening-m1/     ← audio folder                   │
│  │   └── 1-01.ogg                                           │
│  └── ...                                                    │
└─────────────────────────────────────────────────────────────┘
```

### Request lifecycle

When a student opens the catalog page, the server calls `_cached_scan()` which uses `scan_test_headers()` to read only the YAML header and `[module]` attribute lines from each `.md` file (skipping the expensive full parse of passages and questions). The catalog template renders a card per test.

When the student starts a test, `test.html` is served with `TEST_INFO` (the test metadata) embedded as JSON. The JavaScript `app.js` runs on page load and immediately fetches `GET /api/module/<file>?module_index=N` for the first module. The server does a full parse (`parse_test_file` → `build_question_list` → `convert_pages_md_to_html`) and returns the complete page list as JSON. This response is cached by file mtime.


## 2. Backend: Parser

`parser.py` transforms markdown files into structured data. The pipeline is:

```
.md file
  → parse_yaml_header()      extract YAML front matter
  → _RE_MODULE.finditer()     find [module]...[/module] blocks
    → parse_groups()          find [group], [passage], [audio], [question] blocks
      → flush_block()         process each block by type
        → parse_mc_choices()       for mc: extract (A)/(B)/(C)/(D)
        → parse_mc_prompt()        for mc: extract prompt text above choices
        → parse_build_sentence()   for build_sentence: extract Context + Words
        → _RE_CLOZE_ANSWER         for cloze: extract numbered answers
  → build_question_list()     flatten groups into ordered page list
```

### Regex patterns

All 13 regex patterns are pre-compiled at module load as `_RE_*` constants. This avoids recompilation on every line of every file during parsing.

### parse_groups() fast path

The inner loop processes every line of a module's body. Most lines are content inside a `[passage]`, `[transcript]`, or `[question]` block. The fast path checks: if we're inside a block and the line doesn't start with `[/`, it's content — append and skip all structural regex checks.

### Lightweight scan

`scan_test_headers(filepath)` is used by the catalog. It reads the file but only extracts the YAML header and `[module ...]` opening tags (using `_RE_MODULE_ATTRS`), ignoring all body content. This is much faster than a full parse since it skips passage, question, and group processing.

### pending_audio

In `build_question_list()`, when an `[audio src="..."]` block is encountered, its `src` is stored in `pending_audio`. All subsequent questions in the same group inherit this audio until another `[audio]` block overrides it. This means two questions that share a conversation (like Q9 and Q10 both referring to audio `1-09`) automatically get the same audio clip. `pending_audio` is NOT cleared after each question — it persists until the next `[audio]` block or the end of the group.


## 3. Backend: Flask Application

`app.py` provides five routes:

| Route | Method | Returns |
|---|---|---|
| `/` | GET | Catalog page (Jinja2) |
| `/test/<test_id>` | GET | Test-taking page (Jinja2) with `TEST_INFO` JSON embedded |
| `/api/tests` | GET | All tests metadata as JSON |
| `/api/module/<filename>?module_index=N` | GET | Full parsed module as JSON (pages with HTML) |
| `/audio/<path>` | GET | Audio file from tests directory |

### Caching

Three levels of mtime-based caching eliminate redundant work:

**`_cached_scan()`** — For the catalog and `/api/tests`. Checks the latest mtime and file count of all `.md` files in the tests directory. If neither has changed, returns the cached result. File count tracking ensures that file deletions (which don't change remaining files' mtimes) are detected.

**`_cached_parse(filepath)`** — For individual file parsing. Compares the file's current mtime against the cached version.

**`_cached_module_response(filepath, module_index)`** — Caches the complete API response for a module, including the markdown-to-HTML conversion. This is the most expensive operation (parse + flatten + HTML convert) and the most frequently requested endpoint.

### Markdown-to-HTML conversion

`convert_pages_md_to_html()` converts markdown fields (`passage`, `prompt`, `content`, `details.context`) to HTML using Python-Markdown with the `tables` and `nl2br` extensions. A single `_md_instance` is reused across calls (with `.reset()` after each conversion).

For cloze passages, underscore sequences like `manu_______` must be protected from markdown's italic/bold processing. `safe_md_convert()` replaces them with null-byte placeholders before conversion, then restores them after.

### Security

`safe_path(base_dir, user_path)` resolves the real filesystem path and verifies it starts with the tests directory. This prevents path traversal attacks on `/audio/` and `/api/module/` routes (e.g., `/audio/../../etc/passwd` returns 404).


## 4. Frontend: Test Engine

`app.js` (~1056 lines) is the entire test-taking UI. It manages four screens:

```
screen-loading  →  screen-test  →  screen-transition  →  screen-results
                   (one question     (between modules       (final scores
                    per page)         in full test mode)      + download)
```

### Initialization (DOMContentLoaded)

On page load, the script reads `TEST_INFO` (embedded in the HTML by Jinja2) and `URL_PARAMS` (from the query string). Two modes:

**Section mode** (`?mode=section&filename=X&module_index=N`): loads a single module directly.

**Full test mode** (`?mode=full`): chains all modules from `TEST_INFO.modules` in section order (reading → listening → writing → speaking). Checks localStorage for saved playlist progress and offers to resume.

### Module loading

`loadAndStartModule()` fetches the module data from `/api/module/<file>`, sets up the `currentModule` state object, restores any saved progress (answers, page index, timer), and starts the timer and auto-save interval.

### State variables

| Variable | Type | Description |
|---|---|---|
| `playlist` | Array | Ordered list of module descriptors to take |
| `playlistIdx` | Number | Current position in playlist |
| `currentModule` | Object | Active module data (section, pages, timer, etc.) |
| `currentPageIdx` | Number | Current question index within the module |
| `answers` | Object | Current module's answers keyed by question ID |
| `recordings` | Object | Current module's audio recordings (Blob) keyed by question ID |
| `allResults` | Array | Graded results from all completed modules |
| `timerSecondsLeft` | Number | Section timer countdown |
| `playedAudio` | Set | Audio clip IDs already played (for no-replay enforcement) |
| `isRecording` | Boolean | Whether the microphone is currently recording |
| `isFinishing` | Boolean | Guard against double-triggering `finishCurrentModule` |

### Navigation

`renderQuestion()` reads `currentModule.pages[currentPageIdx]` and renders the appropriate UI based on `question_type`. The `body.innerHTML = ''` clears the previous question's DOM before building the new one.

`nextQuestion()` collects the current answer, saves progress, stops any recording, and either advances to the next page or calls `finishCurrentModule()` if on the last page.

`prevQuestion()` is only enabled for reading sections. It collects and saves before going back.

### Rendering by question type

**MC (multiple choice):** In reading section, renders a split-pane layout (passage left, question right) on wide screens. In listening section, renders a single-column layout with an auto-play audio player. Choices are radio buttons with visual highlighting. Keyboard shortcuts A/B/C/D select choices.

**Cloze (fill-in-the-blank):** The passage HTML is post-processed: underscore sequences (`___`) are replaced with `<input>` elements inline. Saved answers are restored into the inputs.

**Build-a-sentence:** Words are displayed as tappable chips in a randomized word bank. Tapping a chip moves it to the sentence slot area. The first word auto-capitalizes. Trailing punctuation (from the answer key) is always visible. Saved answers are restored using a greedy longest-match algorithm that clicks chips in order.

**Email / Discussion (free write):** A textarea with live word count. Saved text is restored.

**Listen-and-repeat / Interview (speaking):** The record button starts disabled ("Listen to the prompt first...") until the audio prompt finishes playing. Once enabled, it pulses to draw attention. Clicking starts recording with a countdown timer. The system detects the best available audio codec (WebM on Chrome/Firefox, MP4 on Safari, OGG as fallback). Recording stops automatically when the timer expires.


## 5. API Response Format

`GET /api/module/<filename>?module_index=N` returns:

```json
{
  "header": {
    "test_id": "practice-test-1",
    "test_name": "2026 New TOEFL Practice Test 1"
  },
  "module_info": {
    "section": "reading",
    "module": 1,
    "timer_minutes": 35
  },
  "audio_dir": "tests/pt1-reading-m1",
  "pages": [
    { /* page object — varies by question_type */ }
  ]
}
```

### Page fields by question type

Every page has these common fields:

| Field | Type | Description |
|---|---|---|
| `group_title` | string | Display name of the question group |
| `question_id` | string | Unique ID within the module (from the `id` attribute) |
| `question_type` | string | One of: `mc`, `cloze`, `build_sentence`, `email`, `discussion`, `listen_repeat`, `interview` |
| `section` | string | `reading`, `listening`, `writing`, or `speaking` |

Type-specific fields:

**mc:**
`prompt`, `prompt_html`, `choices` (object: `{"A": "...", "B": "..."}` ), `answer` (letter), optionally `passage`, `passage_html`, `passage_id`, `audio`

**cloze:**
`passage`, `passage_html`, `passage_id`, `cloze_answers` (array of strings), `answer` (same as `cloze_answers`)

**build_sentence:**
`details` (object: `{"context": "...", "words": ["...", "..."]}`), `answer` (the correct sentence)

**email / discussion:**
`content`, `content_html`, `time_minutes`

**listen_repeat / interview:**
`content`, `content_html`, `audio`, `time_seconds`


## 6. Grading

`gradeModule()` runs client-side after a module finishes. It produces a result object with `score.correct`, `score.total`, and a `details` array.

| Type | Grading method | Counted in score? |
|---|---|---|
| `mc` | Exact match: `userAnswer === page.answer` | Yes |
| `cloze` | Case-insensitive exact match per blank | Yes (each blank is one point) |
| `build_sentence` | Lowercase comparison with punctuation stripped | Yes |
| `email` / `discussion` | Not graded — word count recorded | No |
| `listen_repeat` / `interview` | Not graded — recording presence noted | No |

Correct answers are included in the API response (`page.answer`, `page.cloze_answers`). This enables client-side grading but means students with browser DevTools can see answers. This is a known tradeoff — server-side grading would require a POST endpoint.


## 7. Progress Saving

All progress is stored in browser localStorage, keyed by test ID.

### Storage keys

| Key pattern | Content | Written by |
|---|---|---|
| `toefl_<test_id>_mod_<filename>_<index>` | `{pageIdx, answers, timerSecondsLeft, savedAt}` | `saveModuleProgress()` — every 30s + every page nav |
| `toefl_<test_id>_playlist` | `{playlist, playlistIdx, allResults}` | `savePlaylistState()` — only on module transitions |
| `toefl_<test_id>_complete_<filename>_<index>` | `"1"` | `markModuleComplete()` — when a module finishes |

### Save frequency

Module progress (answers and timer) is saved frequently: every 30 seconds by `autoSave()` and on every page navigation. Playlist state (which module the student is on, accumulated results) is saved only when a module finishes, since it changes only at transitions. This split avoids the overhead of serializing `allResults` on every auto-save.

### Resume flow

On page load in full-test mode, the script checks for a saved playlist. If found and `playlistIdx > 0`, it offers a confirmation dialog. On resume:
1. `playlistIdx` is restored (skipping completed modules)
2. `allResults` from previous modules is restored
3. `loadAndStartModule()` fetches the current module
4. Per-module progress (answers, timer, page) is restored from the module-specific key
5. Per-module progress is cleared when the module finishes

### Limitations

Audio recordings (Blobs) cannot be serialized to localStorage. If the student closes the tab mid-speaking-module and resumes, previously recorded audio from that module is lost. Text answers are preserved.

All writes use `safeSetItem()` which wraps `localStorage.setItem` in try-catch to handle quota exceeded errors gracefully.


## 8. Audio System

### Playback modes

**Auto-play-once (listening section):** The audio plays automatically when the question renders. After playback, the element is removed and replaced with "Audio already played". The `playedAudio` Set tracks which clips have been played. If the browser blocks auto-play (common on mobile), a "Click to Play Audio" fallback button appears.

**Normal (non-listening):** Standard `<audio>` element with browser controls, replayable.

### Shared audio across questions

When multiple questions reference the same audio (e.g., Q9 and Q10 both using `1-09`), the audio plays on Q9. When Q10 renders, `playedAudio.has('1-09')` is true, so it shows "Audio already played" immediately without creating an `<audio>` element (no wasted network request).

### Speaking: record button state machine

```
[disabled: "Listen to prompt first..."]
        │
        ▼ (audio prompt finishes or no audio)
[enabled: "Start Recording" with pulse animation]
        │
        ▼ (click)
[recording: "Stop Recording" with red pulse, countdown timer running]
        │
        ▼ (click or timer expires)
[idle: "Re-record", playback shown]
```

### Codec detection

On page load, the IIFE `detectCodec()` tests `MediaRecorder.isTypeSupported()` for each codec in order: `audio/webm;codecs=opus`, `audio/webm`, `audio/mp4`, `audio/ogg;codecs=opus`, `audio/ogg`. The first supported codec is used for all recordings. This handles Chrome (WebM), Safari (MP4), and Firefox (WebM/OGG).

### Race condition handling

`stopRecording()` returns a Promise that resolves when the MediaRecorder's `onstop` callback fires. `finishCurrentModule()` awaits this Promise before grading, ensuring the final recording blob is saved before results are computed. An `isFinishing` flag prevents the timer expiry and the "Finish" button from both triggering `finishCurrentModule()`.


## 9. Timer System

Two independent timers run during the test:

**Section timer** (`timerSecondsLeft`): Counts down for the entire module. Displayed in the header as `MM:SS`. When it reaches zero, `finishCurrentModule()` is called automatically. Saved to localStorage with module progress so it can be restored on resume. Uses `??` (nullish coalescing) for restore to correctly handle the value `0` (timer expired).

**Question timer** (speaking only): A per-question countdown shown as a progress bar. Starts when the student clicks "Start Recording". When it expires, recording stops automatically.


## 10. Results and Download

After all modules finish (or the single module in section mode), `showFinalResults()` renders:

1. Per-module score breakdowns (correct/total for auto-graded types)
2. Per-question detail rows (✓/✗ with correct answers for wrong ones)
3. Overall score across all modules (in full-test mode)
4. "Download Answers (.zip)" button (if any writing/speaking questions exist)

### ZIP structure

```
<test_id>_answers/
├── reading_M1/
│   └── answers.txt          (scores + per-question results)
├── listening_M1/
│   └── answers.txt
├── writing_M1/
│   └── answers.txt          (includes full email/discussion text)
└── speaking_M1/
    ├── answers.txt
    ├── q1_recording.webm    (audio recordings)
    ├── q2_recording.webm
    └── ...
```

The ZIP is generated client-side using JSZip and saved using FileSaver.js.


## 11. Test Authoring Workflow

The intended pipeline for creating a new test:

1. **Source material:** Start with a TOEFL practice test in `.docx` or PDF format.

2. **Convert to markdown:** Manually (or with AI assistance) write the `.md` file following the format in `FORMAT.md`. Each file needs a YAML header with `test_id` and `test_name`, and one or more `[module]` blocks containing `[group]`, `[passage]`, `[audio]`, and `[question]` blocks.

3. **Write TTS scripts:** For listening and speaking modules, create companion `.tts` files that define the text-to-speech generation instructions (speaker gender, pauses, concatenation). See the TTS section in `FORMAT.md`.

4. **Generate audio:** Run the `.tts` scripts through a TTS API (e.g., Google Cloud TTS, Azure Speech) to produce individual segment files, then concatenate with ffmpeg as specified in the `@@FFMPEG_CONCAT` directives. Output as `.ogg` files.

5. **Place files:** Drop the `.md` files and audio folders into the `tests/` directory. Multiple files sharing the same `test_id` are automatically merged into one test.

6. **Verify:** Refresh the catalog page. The new test appears immediately (no server restart needed — the scan cache invalidates on file changes).


## 12. Deployment

### Development

```bash
pip install -r requirements.txt
python app.py
# Server runs at http://localhost:5000
```

### Production

The Flask development server is single-threaded. For production use:

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

Note: The in-memory caches (`_parse_cache`, `_scan_cache`, `_module_api_cache`) are per-process. With multiple Gunicorn workers, each worker maintains its own cache. This is safe (caches are read-only after population) but means the first request to each worker incurs a cache miss.

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `TOEFL_TESTS_DIR` | `<script_dir>/tests` | Directory containing `.md` test files and audio folders |


## 13. File Reference

| File | Lines | Purpose |
|---|---|---|
| `app.py` | 204 | Flask server: routes, caching, markdown→HTML, path security |
| `parser.py` | 331 | Markdown parser: 13 pre-compiled regexes, lightweight scan, full parse, question flattening |
| `static/js/app.js` | 1056 | Test engine: rendering, grading, timer, audio, recording, navigation, save/restore, zip download |
| `static/css/style.css` | 577 | Dark-themed UI with section-specific color coding |
| `templates/base.html` | 17 | Shared HTML head (fonts, CSS) |
| `templates/catalog.html` | 107 | Test catalog with dropdown per test, progress clearing |
| `templates/test.html` | 76 | Test-taking page with four screen containers |
| `FORMAT.md` | 401 | Complete markdown syntax specification + TTS script format |
| `README.md` | 179 | Project overview, features, quickstart, known limitations |
