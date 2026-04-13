**[English](README.md)** | **[中文](README.zh.md)**

# TOEFL Practice Test System

A web-based TOEFL mock test platform built with Flask and vanilla JavaScript. Teachers author tests in a custom Markdown format; the system parses them into an interactive, timed test-taking interface with auto-grading, audio playback, microphone recording, and downloadable results.

This project is part of **Chao Neng Lu** (超能录), a tutoring program offering AP/A-Level courses, competition prep, JLPT, TOPIK, and TOEFL/IELTS tutoring.

## Prerequisites

- Python 3.9+
- A modern browser (Chrome, Firefox, Safari, or Edge)
- For speaking questions: a microphone and browser mic permission

## Installation

```bash
cd toefl-practice-system
pip install -r requirements.txt
```

## Running the server

```bash
# Default: starts on port 8080
python app.py

# Custom port
python app.py --port 3000
```

Open `http://localhost:8080` in your browser.

### Production deployment

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:8080 app:app
```

Browsers require HTTPS for microphone access on non-localhost domains. Use a reverse proxy (Nginx, Caddy) with SSL for production.

## Usage

### For teachers: creating tests

1. Write `.md` files following the format in `FORMAT.md`.
2. For listening/speaking, place `.ogg` audio files in a folder named after the `.md` file (minus the extension).
3. Drop files into the `tests/` directory. The catalog updates automatically.

A single `.md` file can contain multiple modules of the same section (e.g., Reading Module 1 and Module 2) or even all four sections. Multiple `.md` files sharing the same `test_id` are merged into one test.

### For teachers: generating TTS audio

```bash
python generate_tts_notebook.py tests/*.tts -o tts_generate.ipynb
```

Upload the generated `.ipynb` to Google Colab, select a T4 GPU runtime, and run all cells. The notebook uses Kokoro TTS with `af_heart` (female) and `am_fenrir` (male) voices to produce `.ogg` files.

### For students: taking a test

1. Open `http://localhost:8080` and click a test card.
2. Choose **Take Full Test** (all sections in order) or a **specific section** (e.g., Reading). Choosing a section starts all modules of that section as a chain.
3. Answer one question at a time. The countdown timer turns amber at 5 minutes remaining and red at 1 minute.
4. **Reading:** allows backward navigation. All other sections are forward-only.
5. **Listening:** audio plays once automatically. While the audio is playing, the Next button and answer choices are disabled. No replay.
6. **Speaking:** fully automatic flow. The prompt audio plays, then a 3-second countdown appears ("Recording in 3... 2... 1..."), the microphone activates, and a real-time waveform shows mic input. When the question timer expires, recording stops and the system auto-advances to the next question.
7. **Writing:** type in the text area with live word count.
8. Between different sections, a transition screen shows which section is complete and what comes next. No scores are shown until the end.
9. On the final results screen, scores are shown per section with per-question detail. Cloze blanks show green/red per blank. Click **Download Answers (.zip)** to get text answers and audio recordings.

Progress auto-saves every 30 seconds. Audio recordings cannot be saved across sessions.

### Cloze (fill-in-the-blank) format

Blanks use `prefix[N]suffix` syntax where N is the number of missing letters:

```
manu[7]     → student types 7 letters ("scripts") → "manuscripts"
centu[4]    → student types 4 letters ("ries")    → "centuries"
un[5]able   → student types 5 letters ("avoid")   → "unavoidable"
```

Each blank renders as N individual character boxes. The cursor auto-advances when a box is filled, and wraps from the last blank to the first.

## Project structure

```
toefl-practice-system/
  app.py                    Flask server (routes, caching, path security)
  parser.py                 Markdown test file parser
  generate_tts_notebook.py  Colab notebook generator for TTS audio
  requirements.txt          flask, pyyaml, markdown
  FORMAT.md                 Full Markdown format specification
  LICENSE                   GPL v3
  templates/                Jinja2 templates (catalog, test, base)
  static/css/style.css      Light-themed UI (Apple HIG compliant)
  static/js/app.js          Test engine
  tests/                    Test content (*.md, *.tts, audio folders)
    example-test.md         Example test with all 7 question types
```

## Design

The UI follows Apple Human Interface Guidelines: system font stack (SF Pro / system-ui), 44pt minimum touch targets, clean white/light-gray palette, semantic colors, and animations under 0.3 seconds.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `TOEFL_TESTS_DIR` | `<script_dir>/tests` | Test files and audio directory |

## Known limitations

- Client-side grading: answers are visible in the API response.
- Audio recordings are lost if the browser is closed mid-speaking-module.
- No user authentication; progress is stored in browser localStorage.
- Cloze grading is exact-match only (case-insensitive).

## License

This project is licensed under the **GNU General Public License v3.0**. See [LICENSE](LICENSE) for details. Any derivative work must be distributed under the same license.
