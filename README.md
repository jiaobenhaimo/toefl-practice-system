**[English](README.md)** | **[中文](README.zh.md)**

# Test Practice System

A web-based mock test platform with server-side grading, server-side test sessions, role-based dashboards, and multi-test-type support. Currently used for TOEFL practice, but the architecture supports any standardized test format.

> This project was primarily vibe-coded with [Claude](https://claude.ai) (Anthropic). The architecture, code, and documentation were developed through iterative conversation.

---

## Deployment

### Prerequisites

- Python 3.9+
- A modern web browser (Chrome, Firefox, Safari, Edge)
- HTTPS for microphone access on non-localhost domains

### Quick start

```bash
git clone https://github.com/jiaobenhaimo/toefl-practice-system.git && cd toefl-practice-system

python -m venv venv
source venv/bin/activate        # macOS/Linux
# venv\Scripts\activate         # Windows

pip install -r requirements.txt
python app.py                    # http://localhost:8080
```

On first run, the system creates a SQLite database at `data/toefl.db` and default accounts from `config.yaml`:

| Account | Username | Password | Role |
|---|---|---|---|
| Admin | `admin` | `admin` | Full access |
| Test student | `student` | `student` | Student |

**Change default passwords immediately** at `/admin/users`.

### Production deployment

```bash
source venv/bin/activate
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
gunicorn -w 4 -b 0.0.0.0:8080 app:app
```

Use Nginx or Caddy as a reverse proxy with SSL. Microphone access (for speaking questions) requires HTTPS on all non-localhost domains.

### Configuration

All site-level settings live in `config.yaml`:

```yaml
site:
  name: "My Test Platform"       # Shown in sidebar and titles

default_test_type: toefl          # Fallback test type

test_types:                       # Define as many as needed
  toefl:
    label: "TOEFL iBT"
    sections:
      reading: { label: Reading, color: "#007aff" }
      ...
  ielts:
    label: "IELTS"
    sections: ...

default_admin:                    # Created on first run only
  username: admin
  password: admin
test_account:                     # Optional test student
  username: student
  password: student
```

Environment variables override config where applicable:

| Variable | Default | Description |
|---|---|---|
| `TOEFL_TESTS_DIR` | `./tests` | Directory containing test `.md` files |
| `TOEFL_DB_PATH` | `./data/toefl.db` | SQLite database file path |
| `SECRET_KEY` | random | Flask session encryption key |

---

## Architecture

### Storage

**SQLite** (single file, no external server) with eight tables:

| Table | Purpose |
|---|---|
| `users` | Accounts with hashed passwords and roles |
| `test_results` | Graded submissions with per-section JSON details |
| `test_assignments` | Teacher-to-student assignments with optional section scoping and due dates |
| `test_sessions` | Server-side test progress: answers, timer, page position, recordings, accumulated results |
| `announcements` | Site-wide admin banners |
| `student_notes` | Per-question student notes on results |
| `teacher_comments` | Per-result and per-question teacher feedback |
| `question_explanations` | Teacher-authored explanations per test/question |

Audio recordings are stored on disk at `data/recordings/` (per-result subdirectories).

### Server-side test sessions

For logged-in users, all test progress is stored server-side in the `test_sessions` table. This includes:

- Current module answers (JSON)
- Page position within the current module
- Remaining timer (seconds)
- Per-question time tracking (JSON)
- Accumulated graded results from completed modules (JSON)
- Playlist state (which module in the test sequence)

Audio recordings are uploaded per-module during the test (immediately after each speaking module is graded), stored temporarily in `data/recordings/session_{id}/`, and moved to permanent result storage (`data/recordings/{result_id}/`) when the test finishes.

Guests fall back to `localStorage` for progress tracking (no server-side persistence).

Stale sessions are cleaned up automatically: unfinished sessions older than 7 days and finished sessions older than 30 days are deleted when a new session is created.

### Authentication

Flask sessions with `werkzeug.security` password hashing. "Remember me" extends sessions to 31 days. A guest mode allows anonymous practice without login. Session is regenerated on login to prevent fixation attacks.

### Security

- **CSRF protection** on all form POST routes via session-based tokens. JSON API endpoints are exempt (used by the test engine).
- **Login rate limiting**: 10 attempts per IP per 5-minute window, with automatic memory cleanup of stale IP entries.
- **Answer security**: The `/api/grade` endpoint requires authentication (login or guest session). Correct answers are stripped from the grading response sent to the client — only correct/incorrect status and the user's own answer are visible. Full answer details (with expected values) are stored separately for the result review page.
- **File upload limit**: 16MB max via `MAX_CONTENT_LENGTH`.
- **Password minimum**: 6 characters enforced on creation and change.
- **Open redirect prevention**: login `next` parameter validates relative paths only.
- **Recording access control**: Students can only access their own recordings; teachers/admins can access any.

### Server-side grading

The `/api/module/` endpoint strips correct answers before sending questions to the client. When a student submits, `/api/grade` loads the test server-side, grades each question, and returns results with expected answers removed. Answers never reach the browser in exam mode.

### Multi-test-type support

`config.yaml` defines `test_types` with section labels and colors. Test `.md` files declare their type via `test_type:` in the YAML header. Multiple test formats coexist in the same system.

### User roles

| Role | Pages | Capabilities |
|---|---|---|
| **Admin** | Users, Results, Progress, Dashboard, Catalog | Manage accounts, view all results, assign tests, analytics |
| **Teacher** | Results, Progress, Dashboard, Catalog | View results, assign tests (with due dates), teacher comments, analytics, audio playback |
| **Student** | Assignments, Dashboard, History, Catalog, Account | Take tests, view own analytics and results, change password |
| **Guest** | Catalog | Practice anonymously (results not saved, progress in localStorage only) |

---

## Features

### Test-taking

- Seven question types: multiple choice, cloze (fill-in-the-blank), build-a-sentence, email, academic discussion, listen-and-repeat, interview
- Practice mode: replayable audio, instant answer feedback, timer pause, clearly marked results
- Timer with amber (5 min) and red (1 min) warnings; pause button in practice mode
- Progress dots with question bookmarking (reading)
- Listening lock: controls disabled during audio playback
- Speaking auto-flow: audio → countdown → record with level meter → auto-stop → advance
- Early exit confirmation when time remains
- Keyboard navigation: A/B/C/D for MC answers, arrow keys for prev/next, Enter to advance
- Server-side progress persistence: answers, timer, page position saved every 15 seconds
- Cross-device resume: start a test on one device, continue on another (same account)

### Management

- Admin dashboard: user table, role toggle, create/edit modals, bulk CSV import, announcements
- Teacher dashboard: view all results, assign tests (full or by section) with optional due dates
- Assignment deadlines: teacher sets per-assignment due dates, overdue shown in red
- Student progress tracking with completion percentages
- Self-service password change for all users (6-character minimum)
- Bulk user import via CSV upload (with password validation)
- Announcement banner (admin posts, shown site-wide)
- CSV batch export: teachers select multiple students and download aggregated results

### Review

- Full writing response display: teachers see the complete text students wrote for email/discussion questions
- Audio recording playback: speaking recordings uploaded per-module during the test, playable inline by teachers and students in result review
- Student notes on individual questions
- Teacher comments: overall per-result + per-question feedback visible to students
- Question explanations: from markdown `[explanation]` blocks or teacher web UI, shown after submission

### Analytics

- Student dashboard: score trend line chart, section breakdown bar chart, weakest areas
- Teachers/admins can view analytics for any student via student selector

### Export

- ZIP download with text answers and audio recordings (client-side)
- Server-side PDF export via reportlab (student info, scores, time-per-question)

### Interface

- Sidebar navigation (persistent on desktop, hamburger on mobile)
- Responsive card grid catalog with 3:2 aspect ratio cards
- Modal overlay for test/section selection with time estimates
- Dark mode (system detection + manual toggle, Chart.js theme-aware)
- Chinese/English catalog UI (auto-detect + toggle)
- Apple HIG compliance: 44px touch targets, 12px+ font sizes, `:focus-visible` outlines
- Keyboard-accessible table rows, ARIA labels throughout

---

## Creating tests

Test authoring tools are in `authoring/`:

```bash
cat authoring/FORMAT.md                                    # Format specification
python authoring/generate_tts_notebook.py tests/*.tts -o tts.ipynb  # TTS audio
```

Place `.md` files in `tests/` with audio in matching subfolders. See `tests/example-test.md` for all 7 question types.

To add explanations to questions, place an `[explanation]...[/explanation]` block immediately after a `[question]...[/question]` block in the test markdown. Explanations are shown to students after submission in the result review page. Teachers can also add or override explanations from the web UI.

---

## API reference

### Test engine

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| GET | `/api/module/<filename>` | Guest+ | Load module questions (answers stripped) |
| POST | `/api/grade` | Guest+ | Grade answers server-side (expected answers stripped from response) |
| POST | `/api/save-results` | Login | Save final graded results to DB |

### Server sessions

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| POST | `/api/session/start` | Login | Create or resume a test session |
| POST | `/api/session/<id>/save` | Login | Auto-save progress (answers, timer, page) |
| POST | `/api/session/<id>/advance` | Login | Advance to next module after grading |
| GET | `/api/session/<id>` | Login | Load full session state |
| DELETE | `/api/session/<id>` | Login | Abandon session and clean up recordings |
| POST | `/api/session/<id>/upload-recording` | Login | Upload audio recordings mid-test |
| POST | `/api/session/<id>/finalize-recordings/<result_id>` | Login | Move recordings to permanent storage |

### Recordings

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| POST | `/api/upload-recording/<result_id>` | Login | Upload recordings to result (fallback) |
| GET | `/recordings/<result_id>/<qid>` | Login | Serve a recording file |
| GET | `/api/recordings/<result_id>` | Login | List available recording IDs |

### Review

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| GET/POST | `/api/notes/<result_id>` | Login | Student notes per question |
| GET/POST | `/api/comments/<result_id>` | Login | Teacher comments (POST: teacher/admin only) |
| GET/POST | `/api/explanations/<test_id>` | Login | Question explanations (POST: teacher/admin only) |

### Analytics

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| GET | `/api/analytics/<user_id>` | Login | Score history and section breakdown |

---

## Project structure

```
├── config.yaml               Site and test type configuration
├── app.py                    Flask server, auth, API, sessions, dashboards (~1000 lines)
├── database.py               SQLite module: 8 tables, CRUD, sessions (~560 lines)
├── parser.py                 Markdown test parser (~360 lines)
├── requirements.txt          Python dependencies
├── LICENSE                   GPL v3
├── authoring/
│   ├── FORMAT.md             Test format specification
│   └── generate_tts_notebook.py  Colab TTS generator
├── templates/
│   ├── base.html             Base template, i18n, theme
│   ├── nav.html              Sidebar navigation (role-conditional links)
│   ├── login.html            Login + guest mode
│   ├── catalog.html          Test card grid + modal with time estimates
│   ├── assignments.html      Student assignments with due dates
│   ├── test.html             Test-taking interface
│   ├── history.html          Student history (paginated)
│   ├── result_detail.html    Per-question breakdown, writing text, audio playback,
│   │                         explanations, teacher comments, student notes
│   ├── dashboard.html        Analytics dashboard (Chart.js, dark-mode aware)
│   ├── admin_users.html      User management (create/edit/delete/import)
│   ├── teacher_results.html  Results viewer + test assignment + batch export
│   ├── teacher_progress.html Student progress tracking
│   └── account.html          Password change
├── static/
│   ├── css/style.css         All styles: light + dark, Apple HIG (~540 lines)
│   └── js/app.js             Test engine: timer, audio, recording, sessions (~1760 lines)
├── tests/
│   └── example-test.md       Example test (all 7 question types)
└── data/
    ├── toefl.db              SQLite database (auto-created)
    └── recordings/           Audio recordings (auto-created)
        ├── session_{id}/     Temporary per-session recordings (during test)
        └── {result_id}/      Permanent per-result recordings (after submission)
```

## License

GNU General Public License v3.0. See [LICENSE](LICENSE).
