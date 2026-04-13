**[English](README.md)** | **[中文](README.zh.md)**

# Test Practice System

A web-based mock test platform with user authentication, server-side grading, role-based dashboards, and YAML-driven configuration. Currently configured for TOEFL but designed to support multiple test formats side by side.

Developed for **Chao Neng Lu** (超能录).

## Quick start

```bash
pip install -r requirements.txt
python app.py                # http://localhost:8080
```

Default admin: **admin / admin** — change immediately at `/admin/users`.

## Architecture

**SQLite** stores users, results, and assignments. **Server-side grading** keeps correct answers off the client. **YAML config** defines site branding and test type definitions (TOEFL, IELTS, etc.) for multi-format support.

### User roles

| Role | Can do |
|---|---|
| **Admin** | Manage accounts, view all results, assign tests, manage test files |
| **Teacher** | View results, assign tests, track student progress |
| **Student** | Take assigned tests, browse catalog, view own history and detailed results |
| **Guest** | Browse catalog and practice anonymously (results not saved) |

### Navigation

A sidebar is always visible on desktop (240px) with role-based links: Assignments, Catalog, History, Results, Users. On mobile (≤768px) it collapses behind a hamburger menu. Dark mode and language toggles are in the sidebar footer.

### Test catalog

Tests display as a responsive grid of 3:2 cards that adapts to screen width (4 columns on 14", fewer on smaller screens). Clicking a card opens a dropdown to choose the full test or an individual section.

### Assignments

Teachers assign tests (full or section-only) from `/teacher/results`. Students see their assignments as cards on `/assignments` and can also browse freely at `/catalog`.

### Results

Students view their history at `/history` with clickable rows that open `/results/<id>` — a full breakdown showing check/cross marks, both answers, and time-per-question. Teachers and admins see all student results at `/teacher/results`.

## Configuration

`config.yaml` controls site branding and test type definitions:

```yaml
site:
  name: "TOEFL Practice"
  organization: "Chao Neng Lu"

default_test_type: toefl

test_types:
  toefl:
    label: "TOEFL iBT"
    sections:
      reading: { label: Reading, color: "#3b6fe0" }
      listening: { label: Listening, color: "#7c4fd6" }
      writing: { label: Writing, color: "#1a9f5c" }
      speaking: { label: Speaking, color: "#d06830" }
  ielts:
    label: "IELTS"
    sections: ...
```

To add a new test format, define it under `test_types` and set `test_type:` in the test file's YAML header.

## Creating tests

Test authoring tools are in the `authoring/` directory:

```bash
# See format specification
cat authoring/FORMAT.md

# Generate TTS audio
python authoring/generate_tts_notebook.py tests/*.tts -o tts_generate.ipynb
```

Place `.md` files in `tests/` with audio in matching subfolders.

## Features

- Responsive card grid catalog with 3:2 cards
- Sidebar navigation (desktop persistent, mobile hamburger)
- SQLite database (users, results, assignments)
- Server-side grading (answers never sent to client)
- Section-level test assignments
- Student progress tracking with completion percentages
- Self-service password change for all users
- Student result detail pages with per-question breakdown
- Practice mode with instant feedback and replayable audio
- Chinese/English UI (auto-detect + toggle)
- Dark mode (system detection + toggle)
- PDF export via reportlab
- Time-per-question analytics
- Progress dots with bookmarking
- Seven question types
- YAML configuration for multi-test-type scaling
- ARIA labels, 44pt touch targets (Apple HIG)

## Project structure

```
├── config.yaml              Site + test type configuration
├── app.py                   Flask server + auth + API
├── database.py              SQLite module
├── parser.py                Markdown parser
├── requirements.txt         Dependencies
├── LICENSE                  GPL v3
├── authoring/               Test creation tools
│   ├── FORMAT.md            Markdown format spec
│   └── generate_tts_notebook.py  TTS generator
├── templates/               Jinja2 templates
│   ├── base.html            Base + i18n + theme
│   ├── nav.html             Sidebar navigation
│   ├── login.html           Login page
│   ├── catalog.html         Test catalog (card grid)
│   ├── assignments.html     Student assignments
│   ├── test.html            Test-taking interface
│   ├── history.html         Student history
│   ├── result_detail.html   Result breakdown
│   ├── admin_users.html     Admin user management
│   ├── teacher_results.html Teacher results + assign
│   ├── teacher_progress.html Student progress tracking
│   └── account.html         Password change
├── static/
│   ├── css/style.css        All styles
│   └── js/app.js            Test engine
└── tests/
    └── example-test.md      Example (all 7 question types)
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `TOEFL_TESTS_DIR` | `./tests` | Test files directory |
| `TOEFL_DB_PATH` | `./data/toefl.db` | Database path |
| `SECRET_KEY` | random | Session secret |

## Production

```bash
pip install gunicorn
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
gunicorn -w 4 -b 0.0.0.0:8080 app:app
```

## License

GPL v3. See [LICENSE](LICENSE).
