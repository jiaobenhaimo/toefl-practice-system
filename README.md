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
- **Teacher tools** — assign tests, grade rubrics 0–5, leave per-question comments, monitor live sessions, track progress
- **Parent portal** — read-only view of a child's results, analytics, and teacher feedback
- **One command to run** — Python + SQLite, nothing else to install

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

**Submissions** — browse every student's results, play back speaking recordings, read writing responses. **Assign tests** — pick a student, a test, an optional section and due date. **Score rubrics** — grade speaking and writing 0–5. **Progress Tracking** — click into any student's full history with analytics charts. **Live monitoring** — see which students are currently taking a test with real-time progress.

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

## 📡 API

<details>
<summary>Click to expand API reference</summary>

### Test Engine

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/module/<filename>` | Load questions (answers stripped) |
| POST | `/api/grade` | Grade answers server-side |
| POST | `/api/save-results` | Save graded results |

### Sessions

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/session/start` | Start or resume |
| POST | `/api/session/<id>/save` | Auto-save progress |
| POST | `/api/session/<id>/advance` | Next module after grading |
| GET | `/api/session/<id>` | Load session state |
| DELETE | `/api/session/<id>` | Abandon session |

### Review & Scoring

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/review-data/<id>` | Full review with graded questions |
| GET | `/api/toefl-scores/<id>` | 1–6 band scores |
| POST | `/api/rubric-score/<id>` | Save rubric score (0–5) |
| GET/POST | `/api/notes/<id>` | Student notes |
| GET/POST | `/api/comments/<id>` | Teacher comments |
| GET/POST | `/api/explanations/<test_id>` | Question explanations |
| GET | `/api/analytics/<uid>` | Score history + breakdown |
| GET | `/api/export-pdf/<id>` | PDF report download |
| GET | `/recordings/<id>/<qid>` | Stream a student's recording |

### Spaced Repetition

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/review-queue` | Questions due for review |
| GET | `/api/review-count` | Count for sidebar badge |
| POST | `/api/review-answer/<id>` | Submit review answer |

### Monitoring

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/live-sessions` | Active sessions (teacher only) |

</details>

## 💭 Contributing

Found a bug? Have an idea? [Open an issue](https://github.com/jiaobenhaimo/toefl-practice-system/issues) or pull request.

## 📜 License

GNU General Public License v3.0 — see [LICENSE](LICENSE).
