**[English](README.md)** | **[中文](README.zh.md)**

# Test Practice System

A web-based mock test platform with user authentication, server-side grading, role-based dashboards, and multi-test-type support. Currently used for TOEFL practice, but the architecture supports any standardized test format.

> This project was primarily vibe-coded with [Claude](https://claude.ai) (Anthropic). The architecture, code, and documentation were developed through iterative conversation.

---

## Changelog

### v1.1

**Bug fixes**

- **Practice mode instant feedback**: Fixed a bug where multiple-choice instant feedback in practice mode was broken because the server was stripping correct answers before sending them to the client. Practice mode API calls now include answers so students can see instant correct/incorrect feedback.
- **User deletion data cleanup**: Fixed `delete_user` to also remove associated records in the `student_notes` table. Previously, deleting a user left orphaned notes in the database.

**UI improvements**

- **Larger base font size**: Increased the root font size from 16px to 17px for improved readability across all pages.
- **Users page (admin)**: Increased the "Created" column text size and widened the column. Edit and delete action icons are now larger (22px, up from 18px) for easier interaction.
- **Results page (teacher)**: Drop-down menus in the "Assign Test" form now match the size of the Assign button (consistent padding and font size).
- **Catalog cards**: Cards now use a 3:2 aspect ratio, fill horizontal space with `1fr` grid columns, show 4 cards per row on a 14" screen, and maintain a consistent height based on the aspect ratio rather than a fixed pixel value.

---

## Deployment

### Prerequisites

- Python 3.9+
- A modern web browser (Chrome, Firefox, Safari, Edge)
- HTTPS for microphone access on non-localhost domains

### Quick start

```bash
git clone <repo-url> && cd toefl-practice-system

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
      reading: { label: Reading, color: "#3b6fe0" }
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

**SQLite** (single file, no external server) stores three tables: `users` (accounts with hashed passwords), `test_results` (graded submissions), and `test_assignments` (teacher-to-student assignments with optional section scoping).

### Authentication

Flask sessions with `werkzeug.security` password hashing. "Remember me" extends sessions to 31 days. A guest mode allows anonymous practice without login.

### Server-side grading

The `/api/module/` endpoint strips correct answers before sending questions to the client. When a student submits, `/api/grade` loads the test server-side, grades each question, and returns results. Answers never reach the browser.

### Multi-test-type support

`config.yaml` defines `test_types` with section labels and colors. Test `.md` files declare their type via `test_type:` in the YAML header. Multiple test formats coexist in the same system.

### User roles

| Role | Pages | Capabilities |
|---|---|---|
| **Admin** | Users, Results, Progress, Catalog | Manage accounts, view all results, assign tests |
| **Teacher** | Results, Progress, Catalog | View results, assign tests (full or by section), track progress |
| **Student** | Assignments, Catalog, History, Account | Take tests, view own results, change password |
| **Guest** | Catalog | Practice anonymously (results not saved) |

---

## Features

### Test-taking

- Seven question types: multiple choice, cloze (fill-in-the-blank), build-a-sentence, email, academic discussion, listen-and-repeat, interview
- Practice mode: replayable audio, instant answer feedback (server sends answers to client in practice mode), clearly marked results
- Timer with amber (5 min) and red (1 min) warnings
- Progress dots with question bookmarking (reading)
- Listening lock: controls disabled during audio playback
- Speaking auto-flow: audio → countdown → record with level meter → auto-stop → advance
- Early exit confirmation when time remains

### Management

- Admin dashboard: user table, role toggle, create/edit modals, bulk CSV import, announcements
- Teacher dashboard: view all results, assign tests (full or section-level)
- Student progress tracking with completion percentages
- Self-service password change for all users
- Bulk user import via CSV upload
- Announcement banner (admin posts, shown site-wide)
- Student notes on individual questions in result review

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
│   ├── result_detail.html    Per-question result breakdown
│   ├── admin_users.html      User management (create/edit/delete)
│   ├── teacher_results.html  Results viewer + test assignment
│   ├── teacher_progress.html Student progress tracking
│   ├── account.html          Password change
│   └── teacher_progress.html Student progress tracking
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
