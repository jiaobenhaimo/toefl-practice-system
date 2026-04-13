**[English](README.md)** | **[中文](README.zh.md)**

# Test Practice System

A web-based mock test platform with user authentication, server-side grading, and admin/teacher dashboards. Teachers author tests in Markdown; the system generates timed tests with audio, recording, auto-grading, and PDF export.

Developed for **Chao Neng Lu** (超能录). Currently configured for TOEFL but designed to support other test formats in the future via `config.yaml`.

## Quick start

```bash
pip install -r requirements.txt
python app.py                # http://localhost:8080
```

Default admin: **admin / admin** (change immediately via `/admin/users`).

## Architecture

The application uses SQLite for persistent storage, Flask sessions for authentication, and server-side grading to prevent answer leakage.

### User roles

**Admin** — manages all user accounts, views all results, assigns tests, and manages test files. Dashboard: `/admin/users`.

**Teacher** — views all student results and assigns specific tests to students. Dashboard: `/teacher/results`.

**Student** — takes assigned tests at `/assignments` and browses all tests at `/catalog`. Views personal history at `/history`. Results are saved to the database.

**Guest** — anonymous practice mode entered from the login page. Can browse the catalog and take tests but results are not saved.

### Navigation

The sidebar is always visible on desktop (240px) and hidden behind a hamburger menu on mobile. It shows role-appropriate links: students see Assignments and Catalog; teachers see Catalog and Results; admins see all pages plus Users.

### Test assignments

Teachers assign tests to students from `/teacher/results`. Assigned tests appear on the student's `/assignments` page and launch in test mode. Students can also browse and practice freely from `/catalog`.

### Server-side grading

The `/api/module/` endpoint strips correct answers from the response. When a student finishes a module, the client submits answers to `/api/grade`, which loads the test server-side, grades each question, and returns results. Results are then saved to the database via `/api/save-results`.

## Configuration

All site-level settings are in `config.yaml`:

```yaml
site:
  name: "TOEFL Practice"      # Shown in sidebar and page titles
  organization: "Chao Neng Lu"
test_type: toefl                # For future scaling (ielts, sat, etc.)
sections:                       # Section colors and labels per test type
  toefl:
    reading: { label: Reading, color: "#3b6fe0" }
    ...
```

Environment variables:

| Variable | Default | Description |
|---|---|---|
| `TOEFL_TESTS_DIR` | `./tests` | Test files directory |
| `TOEFL_DB_PATH` | `./data/toefl.db` | SQLite database path |
| `SECRET_KEY` | random | Flask session secret |

## Creating tests

Place `.md` files in `tests/` following `FORMAT.md`. Audio (`.ogg`) goes in a matching subfolder. Seven question types are supported: multiple choice, cloze, build-a-sentence, email, academic discussion, listen-and-repeat, and interview.

## Features

- SQLite database (users, results, assignments)
- Server-side grading (answers never sent to client)
- Sidebar navigation with role-based links
- Test assignments (teacher → student)
- Practice mode with replayable audio and instant feedback
- Chinese/English catalog UI (auto-detect + manual toggle)
- Dark mode (system detection + manual toggle)
- PDF export with student info and time-per-question
- Progress dots with question bookmarking
- Audio buffering, cached mic streams, OGG preferred
- ARIA labels, 44pt touch targets
- YAML-based site configuration for future scaling

## Project structure

```
toefl-practice-system/
  config.yaml               Site configuration
  app.py                    Flask server + auth + API
  database.py               SQLite module
  parser.py                 Markdown parser
  requirements.txt          flask, pyyaml, markdown, reportlab
  data/toefl.db             Database (auto-created)
  templates/
    base.html               Base + i18n + theme
    nav.html                Sidebar navigation
    login.html              Login page
    catalog.html            Test catalog
    assignments.html        Student assignments
    test.html               Test-taking interface
    history.html            Student history
    admin_users.html        Admin user management
    teacher_results.html    Teacher results + assignment
  static/
    css/style.css           All styles
    js/app.js               Test engine
  tests/
    example-test.md         Example test
```

## Production

```bash
pip install gunicorn
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
gunicorn -w 4 -b 0.0.0.0:8080 app:app
```

## License

GPL v3. See [LICENSE](LICENSE).
