**[English](README.md)** | **[中文](README.zh.md)**

# Test Practice System

A web-based mock test platform with user authentication, server-side grading, role-based dashboards, and multi-test-type support. Currently used for TOEFL practice, but the architecture supports any standardized test format.

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

**SQLite** (single file, no external server) stores seven tables: `users` (accounts with hashed passwords), `test_results` (graded submissions with per-section JSON details), `test_assignments` (teacher-to-student assignments with optional section scoping and due dates), `announcements` (site-wide banners), `student_notes` (per-question notes on results), `teacher_comments` (per-result and per-question teacher feedback), and `question_explanations` (teacher-authored explanations stored per test/question).

### Authentication

Flask sessions with `werkzeug.security` password hashing. "Remember me" extends sessions to 31 days. A guest mode allows anonymous practice without login. API endpoints verify result ownership — students can only access their own data.

### Security

- **CSRF protection** on all form POST routes via session-based tokens. JSON API endpoints are exempt (used by the test engine and AJAX features).
- **Login rate limiting**: 10 attempts per IP per 5-minute window.
- **Session regeneration** on login to prevent session fixation.
- **File upload limit**: 16MB max via `MAX_CONTENT_LENGTH`.
- **Password minimum**: 6 characters enforced on change.
- **Open redirect prevention**: login `next` parameter validates relative paths only.

### Server-side grading

The `/api/module/` endpoint strips correct answers before sending questions to the client. When a student submits, `/api/grade` loads the test server-side, grades each question, and returns results. Answers never reach the browser.

### Multi-test-type support

`config.yaml` defines `test_types` with section labels and colors. Test `.md` files declare their type via `test_type:` in the YAML header. Multiple test formats coexist in the same system.

### User roles

| Role | Pages | Capabilities |
|---|---|---|
| **Admin** | Users, Results, Progress, Dashboard, Catalog | Manage accounts, view all results, assign tests, analytics |
| **Teacher** | Results, Progress, Dashboard, Catalog | View results, assign tests (with due dates), teacher comments, analytics |
| **Student** | Assignments, Dashboard, Catalog, History, Account | Take tests, view own analytics, view own results, change password |
| **Guest** | Catalog | Practice anonymously (results not saved) |

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

### Management

- Admin dashboard: user table, role toggle, create/edit modals, bulk CSV import, announcements
- Teacher dashboard: view all results, assign tests (full or by section) with optional due dates
- Assignment deadlines: teacher sets per-assignment due dates, overdue shown in red
- Student progress tracking with completion percentages
- Self-service password change for all users
- Bulk user import via CSV upload
- Announcement banner (admin posts, shown site-wide)
- Student notes on individual questions in result review
- Teacher comments: overall per-result + per-question feedback visible to students
- Question explanations: from markdown `[explanation]` blocks or teacher web UI, shown after submission
- CSV batch export: teachers select multiple students and download aggregated results

### Analytics

- Student dashboard: score trend line chart, section breakdown bar chart, weakest areas
- Teachers/admins can view analytics for any student

### Export

- ZIP download with text answers and audio recordings
- Server-side PDF export via reportlab (student info, scores, time-per-question)

### Interface

- Sidebar navigation (persistent on desktop, hamburger on mobile)
- Responsive card grid catalog
- Modal overlay for test/section selection
- Dark mode (system detection + manual toggle)
- Chinese/English catalog UI (auto-detect + toggle)
- ARIA labels, 44pt touch targets

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

## Project structure

```
├── config.yaml               Site and test type configuration
├── app.py                    Flask server, auth, API, dashboards
├── database.py               SQLite module (users, results, assignments)
├── parser.py                 Markdown test parser
├── requirements.txt          Python dependencies
├── LICENSE                   GPL v3
├── authoring/
│   ├── FORMAT.md             Test format specification
│   └── generate_tts_notebook.py  Colab TTS generator
├── templates/
│   ├── base.html             Base template, i18n, theme
│   ├── nav.html              Sidebar navigation
│   ├── login.html            Login + guest mode
│   ├── catalog.html          Test card grid + modal
│   ├── assignments.html      Student assignments
│   ├── test.html             Test-taking interface
│   ├── history.html          Student history
│   ├── result_detail.html    Per-question result breakdown + explanations + teacher comments
│   ├── dashboard.html        Analytics dashboard (charts, section breakdown)
│   ├── admin_users.html      User management (create/edit/delete)
│   ├── teacher_results.html  Results viewer + test assignment
│   ├── teacher_progress.html Student progress tracking
│   └── account.html          Password change
├── static/
│   ├── css/style.css         All styles (light + dark, sidebar, cards, modal)
│   └── js/app.js             Test engine (timer, audio, recording, grading)
├── tests/
│   └── example-test.md       Example test (all 7 question types)
└── data/
    └── toefl.db              SQLite database (auto-created)
```

## License

GNU General Public License v3.0. See [LICENSE](LICENSE).
