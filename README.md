# 📝 TOEFL Practice System

**[English](README.md)** | **[中文](README.zh.md)**

A self-hosted mock test platform for the **2026 TOEFL iBT** — server-side grading, real-time 1–6 band scoring, and a full teacher dashboard. One command to run, no external database needed.

> Built by [超能录](https://github.com/jiaobenhaimo) with [Claude](https://claude.ai) (Anthropic) through iterative conversation.

## 🌟 Highlights

- **Seven question types** — multiple choice, cloze, build-a-sentence, email, academic discussion, listen-and-repeat, interview
- **2026 TOEFL scoring** — official ETS lookup tables (Speaking /55, Writing /20, Reading & Listening /30 → 1.0–6.0 band)
- **Answers never reach the browser** — server-side grading keeps tests secure
- **Cross-device resume** — start on your laptop, finish on your phone
- **Spaced repetition** — wrong answers auto-collect into a review queue with 1→3→7→14 day intervals
- **Teacher tools** — assign tests with schedule windows, grade rubrics 0–5, leave per-question comments, monitor live sessions, track progress
- **Parent portal** — read-only view of a child's results, analytics, and teacher feedback
- **Per-section analytics** — score trend charts with separate lines for Reading, Listening, Writing, and Speaking
- **Dark mode** — auto-switches based on system preference, with manual override
- **One command to run** — Python + SQLite, nothing else to install
- **Native macOS client** — a SwiftUI companion app is available in the `TOEFLClient/` repository

## ⬇️ Installation

```bash
git clone https://github.com/jiaobenhaimo/toefl-practice-system.git
cd toefl-practice-system
pip install -r requirements.txt
python app.py
```

Open `http://localhost:8080`. Default login: `admin` / `admin`. **Change this immediately** at `/admin/users`.

New user accounts created by an admin are assigned the default password `12345678`. Users should change their password immediately after first login via the Account page.

Python 3.9+ required. Works on macOS, Linux, and Windows. Speaking questions need HTTPS (or localhost) for microphone access.

### Production

```bash
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
gunicorn -w 4 -b 0.0.0.0:8080 app:app
```

Put Nginx or Caddy in front with SSL for microphone access.

## 🚀 Usage

### Students

Log in → see your **Assignments** → take a test → get your **1–6 band score** instantly → review wrong answers with explanations and teacher comments. Wrong answers go into a **Review Queue** with spaced repetition intervals.

### Teachers

**Submissions** — browse every student's results, play back speaking recordings, read writing responses. **Assign tests** — pick a student, a test, an optional section, due date, and schedule window (available from/until). **Score rubrics** — grade speaking and writing 0–5. **Progress Tracking** — click into any student's full history with analytics charts. **Live monitoring** — see which students are currently taking a test with real-time progress.

### Parents

Read-only view of their child's test history, analytics, band scores, and teacher feedback. Admin links each parent account to a student.

### Admins

Everything teachers can do, plus user management (create students, teachers, and parent accounts; bulk CSV import) and site-wide announcements.

## 📐 Scoring (2026 TOEFL iBT)

Implements the official ETS scoring tables from the January 2026 reform:

| Section | Scoring Method | Band Examples |
|---|---|---|
| **Reading / Listening** | % correct → estimated 0–30 | 29–30 → 6.0 · 24–26 → 5.0 · 18–21 → 4.0 |
| **Writing** | Build-a-sentence (1pt, auto) + Email & Discussion (rubric 0–5) → /20 | 19–20 → 6.0 · 15–16 → 5.0 · 11–12 → 4.0 |
| **Speaking** | Listen-and-repeat + Interview (rubric 0–5) → /55 | 52–55 → 6.0 · 42–46 → 5.0 · 32–36 → 4.0 |
| **Overall** | Average of 4 section bands, rounded to nearest 0.5 | |

Practice tests with fewer items are proportionally scaled to the official range before lookup.

## ✍️ Creating Tests

Drop `.md` files into the `tests/` folder:

```markdown
---
test_name: "Reading Practice 1"
test_type: toefl
---
# Reading — Module 1 — 18 min

## Passage
The quick brown fox...

[question]
What does the passage mainly discuss?
- A) Foxes
- B) Speed
- C) Colors
- D) Animals
answer: A
[/question]
```

Audio files go in a subfolder matching the test filename (e.g., `tests/listening-1/track01.ogg`). All 7 question types are documented in the example test.

Add `[explanation]...[/explanation]` after any question to provide an answer explanation shown to students after submission. Teachers can also add or override explanations from the review page.

## 🏗️ Architecture

```
app.py              Flask server — routes, middleware, init
helpers.py          Shared helpers — caching, auth decorators, TOEFL scoring, config
database.py         SQLite — 9 tables, thread-local connection pooling
parser.py           Markdown → structured test data
static/js/app.js    Test engine — timer, audio, recording, keyboard nav
static/css/style.css   Apple HIG design system, light + dark mode
templates/          15 Jinja2 templates
tests/              Markdown test files + audio
data/               SQLite DB + recordings (auto-created)
docs/               User manual
authoring/          Test format spec + TTS audio generator
```

**Security** — CSRF on all forms · login rate limiting · session regeneration · answers stripped from API · recording access control · 16 MB upload limit · open redirect prevention.

**Performance** — thread-local DB connections (1 per request, not 1 per function call) · per-request user caching via flask.g · test file parsing cached by mtime · batch SQL for progress tracking · batch error bank updates · audio compressed to 32 kbps mono Opus · exponential backoff on live monitoring polling.

## ⚙️ Configuration

All settings in `config.yaml`:

```yaml
site:
  name: "My Test Platform"

default_admin:
  username: admin
  password: admin
```

| Environment Variable | Default | Purpose |
|---|---|---|
| `TOEFL_TESTS_DIR` | `./tests` | Where test `.md` files live |
| `TOEFL_DB_PATH` | `./data/toefl.db` | SQLite database path |
| `SECRET_KEY` | random | Flask session encryption |

## 📡 API Reference

The server exposes a JSON API that powers both the web frontend and any external clients. All endpoints are under `/api/` and communicate via JSON.

### Authentication

The API uses **Flask session cookies**. When you log in, the server sets a session cookie that your HTTP client stores automatically. All subsequent requests on the same session are authenticated — no API keys or Bearer tokens needed.

For **browser-based clients** (the built-in web UI), CSRF protection works automatically via the session. For **non-browser clients** (e.g., a native app), JSON POST requests validate the `Origin` header instead of a CSRF token — this means you must send `Content-Type: application/json` on all POST requests and include the session cookie.

**Rate limiting**: Login is limited to 10 attempts per IP address within a 5-minute window. After exceeding this, the server returns `429 Too Many Requests`.

#### Log in

```
POST /api/auth/login
Content-Type: application/json

{"username": "student1", "password": "mypassword"}
```

**Response (200):**

```json
{
  "ok": true,
  "user": {
    "id": 2,
    "username": "student1",
    "display_name": "Alice Chen",
    "role": "student"
  }
}
```

**Error (401):**

```json
{"ok": false, "error": "invalid_credentials"}
```

The response includes a `Set-Cookie` header with the session cookie. Your HTTP client must store and re-send this cookie on all subsequent requests.

#### Check current session

```
GET /api/auth/me
```

Returns the same user object if authenticated, or `401` if not.

#### Log out

```
POST /api/auth/logout
Content-Type: application/json

{}
```

Clears the session. Returns `{"ok": true}`.

### Test Catalog

#### List all tests

```
GET /api/catalog
```

**Response:**

```json
{
  "ok": true,
  "tests": [
    {
      "test_id": "full-practice-1",
      "test_name": "Full Practice Test 1",
      "sections": ["reading", "listening", "writing", "speaking"],
      "total_minutes": 120,
      "modules": [
        {
          "filename": "full-practice-1.md",
          "module_index": 0,
          "section": "reading",
          "module": 1,
          "timer_minutes": 18
        }
      ]
    }
  ]
}
```

Each test lists its sections and modules. The `modules` array contains everything needed to load questions and start a session.

### Loading Questions

#### Get questions for a module

```
GET /api/module/<filename>?module_index=0
GET /api/module/<filename>?module_index=0&practice=true
```

In normal mode, answers are stripped from the response. In practice mode (add `&practice=true`), answers are included for instant self-checking.

**Response:**

```json
{
  "header": {"test_id": "full-practice-1", "test_name": "Full Practice Test 1"},
  "module_info": {"section": "reading", "module": 1, "timer_minutes": 18},
  "pages": [
    {
      "question_id": "r1q1",
      "question_type": "mc",
      "passage": "The quick brown fox...",
      "prompt": "What does the passage mainly discuss?",
      "choices": {"A": "Foxes", "B": "Speed", "C": "Colors", "D": "Animals"}
    }
  ]
}
```

Question types: `mc`, `cloze`, `build_sentence`, `email`, `discussion`, `listen_repeat`, `interview`. Each type has different fields — `mc` has `choices`, `cloze` has `cloze_fills`, `build_sentence` has `details.words`, and writing/speaking types have `content` with the prompt.

### Grading

#### Submit answers for server-side grading

```
POST /api/grade
Content-Type: application/json

{
  "filename": "full-practice-1.md",
  "module_index": 0,
  "answers": {"r1q1": "A", "r1q2": "C"},
  "times": {"r1q1": 45, "r1q2": 30}
}
```

The `answers` object maps question IDs to answers. For MC questions, the value is a letter (A–D). For cloze, it's an array of strings. For build_sentence, it's the assembled sentence. For writing types, it's the essay text. For speaking types, it's `"[audio recorded]"`.

The `times` object maps question IDs to seconds spent (optional, used for analytics).

**Response:**

```json
{
  "section": "reading",
  "moduleNum": 1,
  "score": {"correct": 8, "total": 10},
  "details": [
    {"qid": "r1q1", "type": "mc", "correct": true, "user": "A", "time": 45},
    {"qid": "r1q2", "type": "mc", "correct": false, "user": "C", "time": 30}
  ]
}
```

Note: `expected` answers are never included in the grade response. The server verifies correctness but does not reveal the answer key.

### Sessions (Cross-Device Resume)

Sessions allow students to save progress, switch devices, and resume where they left off.

#### Start or resume a session

```
POST /api/session/start
Content-Type: application/json

{
  "test_id": "full-practice-1",
  "mode": "full",
  "section": null,
  "practice": false,
  "playlist": [
    {"filename": "full-practice-1.md", "module_index": 0, "section": "reading", "module": 1}
  ]
}
```

If an active session already exists for this user/test/mode combination, the server returns the existing session state (resumed). Otherwise, it creates a new session.

**Response:**

```json
{
  "session_id": 42,
  "resumed": false,
  "playlist_idx": 0,
  "answers": {},
  "current_page": 0,
  "timer_left": 1080,
  "question_times": {},
  "completed": []
}
```

**Schedule enforcement**: If a student has a scheduled assignment for this test with a time window, the server checks whether the current time falls within the window. Outside the window, the server returns `403` with `{"error": "not_yet_available"}` or `{"error": "schedule_expired"}`. Practice mode bypasses schedule enforcement.

#### Save progress (auto-save)

```
POST /api/session/<id>/save
Content-Type: application/json

{
  "answers": {"r1q1": "A"},
  "current_page": 3,
  "timer_left": 900,
  "question_times": {"r1q1": 45}
}
```

Call this periodically (the web UI auto-saves every 30 seconds) to persist the student's progress.

#### Advance to next module

```
POST /api/session/<id>/advance
Content-Type: application/json

{
  "section_result": {"section": "reading", "moduleNum": 1, "score": {"correct": 8, "total": 10}, "details": [...]},
  "current_page": 0,
  "timer_left": 1200
}
```

#### Save final results

```
POST /api/save-results
Content-Type: application/json

{
  "test_id": "full-practice-1",
  "test_name": "Full Practice Test 1",
  "practice": false,
  "sections": [...],
  "session_id": 42
}
```

The server re-verifies all answers server-side (ignoring any `correct` field sent by the client), recalculates scores, saves the result, and populates the spaced-repetition error bank for wrong answers.

### Review & Scoring

#### Get review data for a result

```
GET /api/review-data/<result_id>
```

Returns the full test with graded questions, student notes, teacher comments, explanations, recording file list, and section summaries. Used by the review page.

#### Get 1–6 band scores

```
GET /api/toefl-scores/<result_id>
```

**Response:**

```json
{
  "section_bands": {"reading": 5.0, "listening": 4.5, "writing": null, "speaking": null},
  "overall": 4.5,
  "rubric_scores": {"email1": 4, "disc1": 3}
}
```

Sections without rubric scores (speaking, writing tasks not yet graded by a teacher) return `null`.

#### Save rubric score (teacher)

```
POST /api/rubric-score/<result_id>
Content-Type: application/json

{"question_id": "s1q1", "score": "4"}
```

Scores range from 0 to 5. This saves a draft score — it is not visible to the student until published.

#### Publish rubric scores (teacher)

```
POST /api/rubric-submit/<result_id>
Content-Type: application/json

{"module_label": "Speaking M1"}
```

Publishing makes all draft scores for this result visible to the student and creates a notification.

### Analytics

```
GET /api/analytics/<user_id>
```

**Response:**

```json
{
  "score_history": [
    {
      "date": "2026-04-15",
      "pct": 83,
      "band": 5.0,
      "name": "Full Practice Test 1",
      "section_bands": {"reading": 5.0, "listening": 4.5}
    }
  ],
  "section_breakdown": [
    {"section": "reading", "correct": 50, "total": 60, "pct": 83, "band": 5.0},
    {"section": "listening", "correct": 40, "total": 60, "pct": 67, "band": 4.0},
    {"section": "writing", "correct": 0, "total": 0, "pct": 0, "band": null},
    {"section": "speaking", "correct": 0, "total": 0, "pct": 0, "band": null}
  ]
}
```

`score_history` always includes `section_bands` per test result, which the web UI uses to render per-section trend lines (Reading, Listening, Writing, Speaking) on the score trend chart. `section_breakdown` gives aggregate performance across all tests.

### Result History

```
GET /api/my-history?page=1
```

**Response:**

```json
{
  "ok": true,
  "results": [
    {
      "id": 7,
      "test_id": "full-practice-1",
      "test_name": "Full Practice Test 1",
      "practice": false,
      "date": "2026-04-15 14:30:00",
      "total_correct": 45,
      "total_questions": 60,
      "band_overall": 5.0,
      "band_sections": {"reading": 5.0, "listening": 4.5},
      "needs_rubric": true
    }
  ],
  "page": 1,
  "total_pages": 1
}
```

`needs_rubric` is `true` when speaking or writing questions exist that haven't been teacher-scored yet. The web UI shows a ⏳ indicator next to the band score in this case.

### Student Assignments

```
GET /api/my-assignments
```

Returns pending (not-yet-completed) assignments with schedule window information:

```json
{
  "ok": true,
  "assignments": [
    {
      "id": 3,
      "test_id": "full-practice-1",
      "test_name": "Full Practice Test 1",
      "section": null,
      "due_date": "2026-05-01",
      "assigned_at": "2026-04-10 09:00:00",
      "schedule_start": "2026-04-20T09:00",
      "schedule_end": "2026-04-25T17:00",
      "sections": ["reading", "listening", "writing", "speaking"],
      "modules": [...]
    }
  ]
}
```

### Notifications

```
GET /api/notifications
```

**Response:**

```json
{
  "count": 2,
  "items": [
    {"id": 5, "type": "rubric_published", "title": "Scores Published", "message": "Your Speaking M1 scores are ready.", "link": "/review/7", "created_at": "2026-04-15 15:00:00"}
  ]
}
```

#### Mark as read

```
POST /api/notifications/read
Content-Type: application/json

{"ids": [5, 6]}
```

### Spaced Repetition

```
GET /api/review-queue      # Questions due for review
GET /api/review-count      # Count for badge display
POST /api/review-answer/<error_id>   # {"correct": true/false}
```

### Other Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET/POST | `/api/notes/<result_id>` | Student notes per question |
| GET/POST | `/api/comments/<result_id>` | Teacher comments per question |
| GET/POST | `/api/explanations/<test_id>` | Question explanations (teacher-editable) |
| GET | `/api/export-pdf/<result_id>` | PDF report download |
| GET | `/recordings/<result_id>/<question_id>` | Stream a student's audio recording |
| GET | `/api/live-sessions` | Active test sessions (teacher/admin only) |

### Error Codes

| Status | Meaning |
|---|---|
| 200 | Success |
| 400 | Bad request (missing/invalid fields) |
| 401 | Not authenticated (session expired or missing) |
| 403 | Forbidden (wrong role, schedule enforcement, CSRF failure) |
| 404 | Resource not found |
| 429 | Rate limited (login attempts) |

## 💭 Contributing

Found a bug? Have an idea? [Open an issue](https://github.com/jiaobenhaimo/toefl-practice-system/issues) or pull request.

## 📜 License

GNU General Public License v3.0 — see [LICENSE](LICENSE).
