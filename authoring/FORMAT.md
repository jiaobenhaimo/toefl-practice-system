# TOEFL Practice Test Markdown Format Specification

## Overview

Each test is composed of one or more `.md` files, each containing `[module]` blocks wrapped in a YAML-headed document. All files sharing the same `test_id` are grouped into one test in the catalog.

A single `.md` file can contain any combination of modules — all four sections, just one section, or even multiple modules of the same section (e.g. Reading Module 1 and Reading Module 2). The system sorts modules by section order (reading, listening, writing, speaking) and then by module number.

Audio files are stored in a subfolder with the **same basename** as the markdown file.

### Example: separate files (one module each)
```
tests/
├── pt1-reading-m1.md
├── pt1-listening-m1.md
├── pt1-listening-m1/           <- Audio folder
│   ├── 1-01.ogg
│   └── ...
├── pt1-speaking-m1.md
├── pt1-speaking-m1/
│   ├── 3-01.ogg
│   └── ...
├── pt1-writing-m1.md
```

### Example: single file with everything
```
tests/
├── practice-test-1.md          <- Contains all sections + modules
├── practice-test-1/            <- Audio folder for all listening + speaking
│   ├── 1-01.ogg
│   └── 3-01.ogg
```

### Example: one file per section, with multiple modules
```
tests/
├── pt1-reading.md              <- Contains reading M1 and reading M2
├── pt1-listening.md            <- Contains listening M1 and listening M2
├── pt1-listening/
│   ├── 1-01.ogg ... 2-01.ogg
├── pt1-writing.md
├── pt1-speaking.md
├── pt1-speaking/
│   ├── 3-01.ogg ... 4-01.ogg
```

---

## YAML Front Matter (Required)

```yaml
---
test_id: practice-test-1
test_name: "2026 New TOEFL Practice Test 1"
---
```

The body must contain one or more `[module]` blocks:

```
[module section="reading" module=1 timer_minutes=35]
...groups go here...
[/module]

[module section="listening" module=1 timer_minutes=36]
...groups go here...
[/module]

[module section="writing" module=1 timer_minutes=30]
...groups go here...
[/module]

[module section="speaking" module=1 timer_minutes=20]
...groups go here...
[/module]
```

You can also include multiple modules of the **same section** in one file:

```
[module section="reading" module=1 timer_minutes=35]
...reading module 1 groups...
[/module]

[module section="reading" module=2 timer_minutes=35]
...reading module 2 groups...
[/module]
```

### Fields

| Field           | Type    | Required | Description                                       |
|-----------------|---------|----------|---------------------------------------------------|
| `test_id`       | string  | yes      | Groups files into the same test                   |
| `test_name`     | string  | yes      | Human-readable test name                          |
| `section`       | string  | yes      | One of: `reading`, `listening`, `speaking`, `writing` |
| `module`        | integer | yes      | Module number (1, 2, ...)                         |
| `timer_minutes` | number  | yes      | Time limit in minutes; 0 for untimed              |

---

## Audio File Naming

Format: `M-XX.ogg` where:
- `M` = part identifier: `1` (Listening M1), `2` (Listening M2), `3` (Speaking)
- `XX` = zero-padded sequence number (01, 02, ...)

Stored in a folder named the same as the `.md` file:
`pt1-listening-m1/1-01.ogg` for `pt1-listening-m1.md`

---

## Body Syntax

The body uses standard markdown with special block markers in square brackets.

### Group Block

Groups questions under a displayed heading. Every question must be inside a group.

```
[group title="Fill in the missing letters in the paragraph"]
...passages and questions...
[/group]
```

### Passage Block

A block of text shown alongside its questions. An `id` is required.

```
[passage id="email-1"]
**To:** chen.studentlife@dmail.com
**From:** riverside.dining@dmail.com
...
[/passage]
```

### Audio Block (single line, self-closing)

References an audio file to be played. The `src` value is the filename stem (without `.ogg`).

```
[audio src="1-01"]
```

### Transcript Block

Contains the transcript of an audio clip. Stored for reference, **not displayed** to the student during the test.

```
[transcript]
Woman: Do you know if the campus bookstore is still open?
[/transcript]
```

### Question Block

Defines a single question. Attributes vary by type.

```
[question id=1 type="mc" answer="B" passage="email-1"]
What is the main purpose of the email?
(A) Option A
(B) Option B
(C) Option C
(D) Option D
[/question]
```

#### Question Attributes

| Attribute      | Required | Description                                     |
|----------------|----------|-------------------------------------------------|
| `id`           | yes      | Unique question number within the module        |
| `type`         | yes      | Question type (see below)                       |
| `answer`       | varies   | Correct answer (for auto-graded types)          |
| `audio`        | no       | Audio file stem to play for this question       |
| `passage`      | no       | ID of the passage this question belongs to      |
| `time_seconds` | no       | Response time limit (speaking questions)        |
| `time_minutes` | no       | Suggested writing time (email/discussion)       |

---

## Question Types

| Type             | Auto-graded | Description                                   |
|------------------|-------------|-----------------------------------------------|
| `mc`             | yes         | Multiple choice (A/B/C/D)                     |
| `cloze`          | yes         | Fill-in-the-missing-letters paragraph          |
| `build_sentence` | yes         | Reorder words to form a sentence              |
| `email`          | no          | Write an email (free text)                    |
| `discussion`     | no          | Academic discussion post (free text)          |
| `listen_repeat`  | no          | Listen to audio, then record spoken response  |
| `interview`      | no          | Listen to interviewer, record spoken response |

---

## Type-Specific Formats

### `mc` (Multiple Choice)

```
[question id=11 type="mc" answer="B" passage="email-1"]
What is the main purpose of the email?
(A) To announce a change in dining hall staff
(B) To inform about extended hours during finals week
(C) To promote new menu items
(D) To request feedback on dining services
[/question]
```

### `cloze` (Fill in Missing Letters)

Blanks are marked with `[N]` where N is the number of missing letters. The blank can have a prefix and/or suffix attached to show the known parts of the word. Answers are listed as a numbered list of **complete words** in the question block.

The syntax is: `prefix[N]suffix`
- `prefix` — known letters before the blank (optional)
- `N` — number of missing letters
- `suffix` — known letters after the blank (optional)

The UI renders N individual character input boxes. When the student fills all N characters, the cursor auto-advances to the next blank. The system grades by checking if `prefix + typed_letters + suffix` matches the full answer word (case-insensitive).

```
[passage id="cloze-1"]
We know from ancient manu[7] that have been preserved for centu[4] that early civil[8] developed complex writ[3] systems.
[/passage]

[question id=1 type="cloze" passage="cloze-1"]
1. manuscripts
2. centuries
3. civilizations
4. writing
[/question]
```

In this example:
- `manu[7]` = 7 blank letters → student types "scripts" → "manuscripts"
- `centu[4]` = 4 blank letters → student types "ries" → "centuries"
- `civil[8]` = 8 blank letters → student types "izations" → "civilizations"
- `writ[3]` = 3 blank letters → student types "ing" → "writing"

You can also place blanks mid-word with a suffix: `un[5]able` (answer: "unavoidable", student types "avoid").

### `build_sentence`

```
[question id=1 type="build_sentence" answer="Which store has the best deals?"]
**Context:** I need to buy a new laptop.
**Words:** has / the best / which / store / deals
[/question]
```

### `email` / `discussion`

```
[question id=11 type="email" time_minutes=7]
**Scenario:** Your coworker, Kevin, recently recommended...

Write an email to Kevin. In your email, do the following:
- Explain what was wrong with the restaurant
- Describe the team's reaction to the visit

**To:** Kevin
**Subject:** Team Lunch Experience
[/question]
```

### `listen_repeat` / `interview`

```
[audio src="3-01"]
[transcript]
Supervisor: Welcome to our bookstore café.
[/transcript]

[question id=1 type="listen_repeat" audio="3-01" time_seconds=15]
Listen and repeat what you hear.
[/question]
```

---

## TTS Audio Generation Script

When parsing a TOEFL test into markdown, you must also produce a **TTS generation script** file alongside each listening/speaking module. This script contains all text that needs to be synthesized into audio, with machine-readable markers.

### Output file naming

For each `.md` file that has audio, produce a companion `.tts` file:
```
pt1-listening-m1.md   →   pt1-listening-m1.tts
pt1-speaking-m1.md    →   pt1-speaking-m1.tts
```

### TTS script format

The `.tts` file uses the following machine-readable delimiters:

```
@@TTS_FILE_BEGIN id="1-01" output="1-01.ogg"
@@SEGMENT_BEGIN speaker="female" segment_file="1-01.ogg"
Do you know if the campus bookstore is still open?
@@SEGMENT_END
@@TTS_FILE_END

@@TTS_FILE_BEGIN id="1-09" output="1-09.ogg"
@@SEGMENT_BEGIN speaker="female" segment_file="1-09-a.ogg"
Hey Mark, did you finish the research paper for Professor Chen's class?
@@SEGMENT_END
@@SEGMENT_BEGIN speaker="male" segment_file="1-09-b.ogg"
Not yet. I'm having trouble finding reliable sources for my topic on renewable energy. The library database seems limited.
@@SEGMENT_END
@@SEGMENT_BEGIN speaker="female" segment_file="1-09-c.ogg"
Have you tried accessing the digital archives? They have a lot of recent studies. Plus, you can request articles from other universities through interlibrary loan.
@@SEGMENT_END
@@SEGMENT_BEGIN speaker="male" segment_file="1-09-d.ogg"
Really? How long does that usually take?
@@SEGMENT_END
@@SEGMENT_BEGIN speaker="female" segment_file="1-09-e.ogg"
About 3-5 days for most requests. Since your paper isn't due until next Friday, you should have plenty of time.
@@SEGMENT_END
@@FFMPEG_CONCAT segments="1-09-a.ogg,1-09-b.ogg,1-09-c.ogg,1-09-d.ogg,1-09-e.ogg" output="1-09.ogg"
@@TTS_FILE_END
```

### Delimiter reference

| Delimiter            | Description                                                              |
|----------------------|--------------------------------------------------------------------------|
| `@@TTS_FILE_BEGIN`   | Start of one audio file. `id` = identifier, `output` = final filename.   |
| `@@TTS_FILE_END`     | End of one audio file block.                                             |
| `@@SEGMENT_BEGIN`    | Start of one TTS segment. `speaker` = `male` or `female`. `segment_file` = filename for this segment's audio. |
| `@@SEGMENT_END`      | End of one TTS segment. All text between BEGIN and END is spoken.        |
| `@@FFMPEG_CONCAT`    | Required when file has multiple segments. `segments` = comma-separated ordered list of segment files. `output` = final combined filename. |

### Rules

1. **Single-speaker files** (e.g. a one-line question prompt): produce ONE `@@SEGMENT_BEGIN/END` block. No `@@FFMPEG_CONCAT` is needed — the single segment file IS the output file (use the same name for both `segment_file` and `output`).

2. **Multi-speaker files** (e.g. a conversation between two people): produce one `@@SEGMENT_BEGIN/END` per spoken turn. Each segment gets a unique `segment_file` name using the pattern `{id}-{letter}.ogg` (e.g. `1-09-a.ogg`, `1-09-b.ogg`). End with `@@FFMPEG_CONCAT` listing all segments in order.

3. **Speaker gender** must be exactly `male` or `female` in the `speaker` attribute. This is used to select the TTS voice.

4. **Text between SEGMENT_BEGIN and SEGMENT_END** is the exact text to be spoken. Do not include speaker labels (like "Woman:" or "Man:") — only the spoken words. Do not include stage directions or pauses.

5. **Pauses**: If a pause is needed (e.g. "[2-second pause]" in the original), insert a `@@PAUSE seconds=2` line between segments. The ffmpeg concatenation should insert silence of the specified duration.

6. **The `id` in TTS_FILE_BEGIN must match the `src` in the `[audio]` block** in the corresponding markdown file.

### Processing the TTS script

A processing script should:
1. Parse each `@@TTS_FILE_BEGIN` ... `@@TTS_FILE_END` block.
2. For each `@@SEGMENT_BEGIN`, call your TTS engine with the text and the specified speaker gender. Save to `segment_file`.
3. If `@@FFMPEG_CONCAT` is present, concatenate segments:
   ```bash
   # Create file list
   echo "file '1-09-a.ogg'" > list.txt
   echo "file '1-09-b.ogg'" >> list.txt
   echo "file '1-09-c.ogg'" >> list.txt
   # Concatenate
   ffmpeg -f concat -safe 0 -i list.txt -c copy 1-09.ogg
   ```
4. If `@@PAUSE` is between segments, generate a silent audio file of the specified duration and include it in the concatenation list:
   ```bash
   ffmpeg -f lavfi -i anullsrc=r=48000:cl=mono -t 2 -q:a 5 pause_2s.ogg
   ```
5. Delete intermediate segment files after concatenation if desired.

### Complete example: Listening Module 1

```
@@TTS_FILE_BEGIN id="1-01" output="1-01.ogg"
@@SEGMENT_BEGIN speaker="female" segment_file="1-01.ogg"
Do you know if the campus bookstore is still open?
@@SEGMENT_END
@@TTS_FILE_END

@@TTS_FILE_BEGIN id="1-02" output="1-02.ogg"
@@SEGMENT_BEGIN speaker="male" segment_file="1-02.ogg"
How was your presentation in marketing class?
@@SEGMENT_END
@@TTS_FILE_END

@@TTS_FILE_BEGIN id="1-09" output="1-09.ogg"
@@SEGMENT_BEGIN speaker="female" segment_file="1-09-a.ogg"
Hey Mark, did you finish the research paper for Professor Chen's class?
@@SEGMENT_END
@@SEGMENT_BEGIN speaker="male" segment_file="1-09-b.ogg"
Not yet. I'm having trouble finding reliable sources for my topic on renewable energy. The library database seems limited.
@@SEGMENT_END
@@SEGMENT_BEGIN speaker="female" segment_file="1-09-c.ogg"
Have you tried accessing the digital archives? They have a lot of recent studies. Plus, you can request articles from other universities through interlibrary loan.
@@SEGMENT_END
@@SEGMENT_BEGIN speaker="male" segment_file="1-09-d.ogg"
Really? How long does that usually take?
@@SEGMENT_END
@@SEGMENT_BEGIN speaker="female" segment_file="1-09-e.ogg"
About 3-5 days for most requests. Since your paper isn't due until next Friday, you should have plenty of time.
@@SEGMENT_END
@@FFMPEG_CONCAT segments="1-09-a.ogg,1-09-b.ogg,1-09-c.ogg,1-09-d.ogg,1-09-e.ogg" output="1-09.ogg"
@@TTS_FILE_END
```

### Complete example: Speaking Module 1

```
@@TTS_FILE_BEGIN id="3-01" output="3-01.ogg"
@@SEGMENT_BEGIN speaker="female" segment_file="3-01.ogg"
Welcome to our bookstore café.
@@SEGMENT_END
@@TTS_FILE_END

@@TTS_FILE_BEGIN id="3-09" output="3-09.ogg"
@@SEGMENT_BEGIN speaker="male" segment_file="3-09.ogg"
Thank you for participating in our study today. I'm researching how students approach learning and studying. I'd like to ask you some questions. First, do you prefer studying alone or with other people? Why?
@@SEGMENT_END
@@TTS_FILE_END
```
