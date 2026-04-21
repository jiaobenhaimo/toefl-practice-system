# Test Markdown Format — Authoring Specification

> **Audience.** This document is written for an AI agent converting existing standardized-test PDFs into the test-markdown format consumed by the practice system. It is precise enough that a well-behaved parser and a well-behaved author agree on every byte. When this spec and `parser.py` disagree, **the spec is authoritative** and `parser.py` has a bug.

All rules in this document are normative. "MUST", "MUST NOT", "SHOULD", "MAY" follow RFC 2119 semantics.

---

## 1. File layout

### 1.1 File set

A single test is identified by a `test_id` string. A test is composed of one or more UTF-8 `.md` files, all sharing the same `test_id`. The runtime discovers tests by scanning `tests/*.md` (non-recursive) and grouping by `test_id`.

```
tests/
├── <basename>.md        (one or more per test_id)
└── <basename>/          (audio folder, same name as its .md, NO .md extension)
    └── <audio-files>.ogg
```

The audio folder for a given `.md` file is the directory sibling of the `.md` with the **same basename** (extensionless). Audio folders MUST NOT be shared between `.md` files, even in the same test.

### 1.2 Filename rules

- The `.md` file's basename MUST consist of characters matching `[A-Za-z0-9._-]+`. No spaces, no forward slashes.
- Hidden files and files beginning with `README` are ignored by the scanner.
- The basename MUST be unique within `tests/`.

### 1.3 Encoding and line endings

- Files MUST be encoded as UTF-8 without BOM.
- Line endings MAY be LF or CRLF; the parser normalizes to LF.
- Trailing whitespace on any line MUST be preserved inside `[passage]` and `[question]` content blocks and MAY be trimmed elsewhere. Authors SHOULD NOT rely on trailing whitespace carrying meaning.

### 1.4 Grouping multiple files into one test

Any `.md` files sharing the same `test_id` (see §2) are grouped. Within a test, modules from all files are merged and then sorted by:

1. Section order (fixed): `reading` = 0, `listening` = 1, `writing` = 2, `speaking` = 3.
2. Module number (ascending integer).

If two modules tie on `(section, module)`, the one from the `.md` file whose filename sorts earliest (bytewise) wins; the other is loaded but its order relative to the winner is undefined. Authors SHOULD NOT create ties.

---

## 2. YAML front matter

Every `.md` file MUST begin with a YAML front-matter block delimited by exactly `---` on its own line above and below the YAML.

```yaml
---
test_id: practice-test-1
test_name: "2026 Practice Test 1"
---
```

### 2.1 Required fields

| Field       | Type   | Value constraints |
|-------------|--------|-------------------|
| `test_id`   | string | Matches `[A-Za-z0-9._-]+`. Acts as the grouping key. |
| `test_name` | string | Human-readable; displayed in the catalog. UTF-8. No length limit. |

### 2.2 Unknown fields

Unknown YAML fields MUST be ignored by the parser. Authors SHOULD NOT add fields the spec does not define.

### 2.3 Whitespace and quoting

YAML's own rules apply. Values containing `:`, `#`, `'`, `"`, leading/trailing whitespace, or starting with `-`/`?`/`!` MUST be quoted. When in doubt, quote.

---

## 3. Body grammar

The body follows the front matter. Its grammar is:

```
body         ::= WS* module+ WS*
module       ::= "[module" WS attrs "]" module-body "[/module]"
module-body  ::= (WS | group)*
group        ::= "[group" WS attrs "]" group-body "[/group]"
group-body   ::= (WS | passage | audio | transcript | question | explanation)*
passage      ::= "[passage" WS attrs "]" content "[/passage]"
audio        ::= "[audio" WS attrs "]"                        /* self-closing */
transcript   ::= "[transcript]" content "[/transcript]"       /* no attrs */
question     ::= "[question" WS attrs "]" content "[/question]"
explanation  ::= "[explanation]" content "[/explanation]"     /* no attrs */
attrs        ::= attr (WS attr)*
attr         ::= name "=" value
name         ::= [A-Za-z_][A-Za-z0-9_]*
value        ::= quoted | bareword
quoted       ::= "\"" <any UTF-8 except "\"" and newline>* "\""
bareword     ::= [A-Za-z0-9._-]+
WS           ::= [ \t\r\n]
content      ::= <any UTF-8 until the matching close tag>
```

### 3.1 Tag placement rules (normative)

These rules are not all captured by the grammar above. A parser MUST enforce them:

1. **Tags MUST occupy the full line.** An opening tag (`[module …]`, `[group …]`, `[passage …]`, `[question …]`, `[transcript]`, `[explanation]`) and every closing tag (`[/module]`, `[/group]`, `[/passage]`, `[/question]`, `[/transcript]`, `[/explanation]`) MUST appear on their own line with only optional leading/trailing whitespace. The self-closing `[audio …]` tag also MUST occupy its own line.

2. **No nesting** of `[passage]`, `[transcript]`, `[question]`, or `[explanation]` blocks. A parser encountering a block-opening tag while already inside a content block MUST treat the inner tag as literal text.

3. **Scope nesting is fixed** at `module > group > (passage | audio | transcript | question | explanation)`. `[group]` outside of `[module]`, or `[passage]` outside of `[group]`, is a format error.

4. **Every `[question]` MUST live inside a `[group]`.** The parser discards questions not inside a group without warning.

5. **`[explanation]` blocks attach to the immediately preceding `[question]`** within the same group. An `[explanation]` with no preceding question in the group is silently discarded.

### 3.2 Attribute quoting

- Attribute names MUST match `[A-Za-z_][A-Za-z0-9_]*`.
- Attribute values MAY be:
  - **Quoted**: `key="value"` — double quotes only, no embedded double quotes (not even escaped), no embedded newlines.
  - **Bareword**: `key=value` — where `value` matches `[A-Za-z0-9._-]+`.
- For values containing any character outside `[A-Za-z0-9._-]`, authors MUST use the quoted form. In particular, any attribute value containing spaces MUST be quoted.
- When in doubt, quote. Parsers MUST accept both forms.
- `key='value'` (single quotes) is NOT supported. Authors MUST NOT use it.

### 3.3 Ordering inside a group

Within a `[group]`, items appear in **reading order**. Rendering preserves the author's order of questions. Passages, however, are addressed by `id` (see §4.2) and their textual position within the group does not matter.

`[audio src="…"]` tags establish "pending audio" for every subsequent `[question]` in the same group until another `[audio]` resets it. A `[question]` MAY override the pending audio with its own `audio="…"` attribute.

---

## 4. Block reference

### 4.1 `[module]`

Attributes:

| Attribute       | Required | Type    | Description |
|-----------------|----------|---------|-------------|
| `section`       | yes      | enum    | One of: `reading`, `listening`, `writing`, `speaking`. Lowercase only. |
| `module`        | yes      | integer | Positive integer. Unique per `(test_id, section)`. |
| `timer_minutes` | yes      | integer | Time limit for the module in minutes. `0` means untimed. |

A single `.md` file MAY contain multiple `[module]` blocks, including multiple modules of the same section.

### 4.2 `[passage]`

Attributes:

| Attribute | Required | Type   | Description |
|-----------|----------|--------|-------------|
| `id`      | yes      | string | Matches `[A-Za-z0-9._-]+`. Unique within its group. Referenced by questions via `passage="…"`. |

The content between `[passage id="…"]` and `[/passage]` is rendered as Markdown (see §6) when shown alongside a question.

- A passage with a given `id` is usable **only by questions in the same `[group]`**. Parsers MUST NOT resolve `passage="x"` across groups.
- An unresolved `passage="x"` on a question is silently dropped: the question is still shown, but without a passage. Authors SHOULD avoid dangling references.
- Duplicate `id` within a group: the later `[passage]` block wins. Authors MUST NOT rely on this.

### 4.3 `[audio]` (self-closing)

Attributes:

| Attribute | Required | Type   | Description |
|-----------|----------|--------|-------------|
| `src`     | yes      | string | Matches `[A-Za-z0-9._-]+`. The audio file's **stem** (basename without extension). The parser appends `.ogg` at runtime. |

Resolution: at runtime the audio is fetched from `/audio/<md-basename>/<src>.ogg`. The file MUST exist in the audio folder for this `.md` file.

- `[audio]` MUST appear inside a `[group]` (never at module top level).
- The tag is self-closing. There is no `[/audio]`.
- The audio "sticks" to all subsequent questions in the group until another `[audio]` tag replaces it, or until the group ends.
- A `[question]` MAY override the pending audio with its own `audio="…"` attribute. Doing so does not clear the pending audio for the *next* question.

### 4.4 `[transcript]`

Takes no attributes. The content is the transcript of the immediately preceding `[audio]` block.

- Transcripts are stored for reference but **never shown** to the test-taker during a test. They are displayable in the Review view if a future UI adds one.
- A transcript with no preceding audio in the group is still stored but is unreferenced.
- Only one transcript per audio is expected; additional ones are appended but unreferenced.

### 4.5 `[question]`

Attributes (common to all question types):

| Attribute      | Required | Type    | Description |
|----------------|----------|---------|-------------|
| `id`           | yes      | integer | Positive integer unique within its **module**. Not unique across modules in the same file, not unique across test files. |
| `type`         | yes      | enum    | See §5 for the enumeration. Lowercase only. |
| `answer`       | varies   | string  | Correct answer for auto-graded types. See §5 for format per type. |
| `audio`        | no       | string  | Overrides the group's pending audio. Matches `[A-Za-z0-9._-]+`. |
| `passage`      | no       | string  | Id of a passage in the same group. |
| `time_seconds` | no       | integer | Speaking response duration (only `listen_repeat`, `interview`). Default: `30`. |
| `time_minutes` | no       | integer | Suggested writing duration (only `email`, `discussion`). Default: not shown. |

The content of the block is type-specific; see §5.

### 4.6 `[explanation]`

Takes no attributes. The content is Markdown (see §6) that will be shown to the test-taker **only in Review mode** after they complete the test.

- An `[explanation]` attaches to the **most recent preceding `[question]` in the same group**.
- An `[explanation]` with no preceding question in the group is silently dropped.
- If multiple `[explanation]` blocks follow a single question, only the last one survives (it overwrites).
- Explanations from the markdown MAY be overridden at runtime by teacher-authored explanations stored in the database; the DB version wins if present.

### 4.7 `[group]`

Attributes:

| Attribute | Required | Type   | Description |
|-----------|----------|--------|-------------|
| `title`   | yes      | string | Heading shown above the group in the test UI. Plain text (no Markdown rendering). |

Groups are purely organizational. They scope passage `id`s and audio-stickiness. They do not affect grading.

---

## 5. Question types

### 5.1 Enumeration

| `type`           | Auto-graded | Required fields | `answer` format |
|------------------|-------------|-----------------|-----------------|
| `mc`             | yes         | `answer` | Single uppercase letter `A`–`D` |
| `cloze`          | yes         | — (answers listed in content) | N/A — in body |
| `build_sentence` | yes         | `answer` | The target sentence (string) |
| `email`          | no (rubric) | —   | N/A |
| `discussion`     | no (rubric) | —   | N/A |
| `listen_repeat`  | no (rubric) | —   | N/A |
| `interview`      | no (rubric) | —   | N/A |

Unknown `type` values cause the question to be parsed but flagged as type `mc` by default, with no choices — effectively unanswerable. Authors MUST NOT use unknown types.

### 5.2 `mc` (multiple choice)

Body format:

```
[question id=11 type="mc" answer="B" passage="email-1"]
Prompt text, may span multiple lines.

Extra prompt paragraphs are allowed.
(A) First option
(B) Second option
(C) Third option
(D) Fourth option
[/question]
```

Rules:

- The **prompt** is every line before the first line whose trimmed content begins with `(A)`, `(B)`, `(C)`, or `(D)`. Any Markdown allowed.
- The **choices** are parsed by finding lines matching the regex `^\s*\(([A-D])\)\s*(.+)$` anywhere in the content. Order in the rendered UI is A, B, C, D (dictionary order), regardless of write order.
- Exactly four choices labelled A, B, C, D are expected. Missing labels produce a question with fewer choices. Duplicate labels: the later definition wins. Authors MUST provide all four.
- `answer` MUST be exactly `A`, `B`, `C`, or `D`. Case-sensitive — `b` will never match.
- Trailing whitespace inside a choice text is trimmed.

### 5.3 `cloze` (fill-in-the-missing-letters)

A cloze question pairs a passage containing blank markers with an ordered list of full-word answers.

Blank marker syntax in the passage: `prefix[N]suffix` where:

- `prefix` — optional lowercase letters already visible before the blank. Matches `\w*`.
- `N` — positive integer, the number of missing characters the student will type.
- `suffix` — optional lowercase letters already visible after the blank. Matches `\w*`.

Body of the question block is a numbered list of **complete words**:

```
[passage id="cloze-1"]
Many ancient manu[7] were preserved for centu[4].
[/passage]

[question id=1 type="cloze" passage="cloze-1"]
1. manuscripts
2. centuries
[/question]
```

Rules:

- The answer list is discovered by scanning the question content with the regex `^\s*\d+\.\s*(.+)$` on each line. The *numeric label* in the list is informational — what matters is order. `1.`, `2.`, `3.` need not be consecutive; parser uses positional order.
- The Nth blank in the passage (reading-order, left to right, top to bottom) is paired with the Nth answer in the list.
- If the passage contains **more blanks than answers**, the extra blanks are paired with empty-string answers and will never grade correct. Authors MUST NOT ship this state.
- If the passage contains **fewer blanks than answers**, the extra answers are ignored. Authors MUST NOT ship this state.
- `N` in `[N]` MUST equal `len(full_answer) - len(prefix) - len(suffix)`. If it does not match, the expected fill is computed from the full answer minus the prefix/suffix, and `N` is ignored for grading (but the UI renders exactly `N` boxes, so a mismatch means the student cannot type the correct number of letters). Authors MUST ensure `N` is correct.
- Prefix and suffix matching is **case-insensitive**; the full answer is matched case-insensitively too. Displayed cloze fills in the UI are lowercase.
- A passage that is used with a non-`cloze` question MAY contain a sequence that looks like `[N]` (e.g. `[3]` as a citation). In that case it is not interpreted as a blank — the cloze blank detection runs **only when the question using the passage is of type `cloze`**. If the same passage is used by both a cloze question and a non-cloze question, the cloze question will treat `[N]` as blanks. Authors SHOULD NOT mix these.
- There is no escape for a literal `[N]` inside a cloze passage. Authors MUST avoid literal `[digit]` substrings inside cloze passages.
- The `answer` attribute MUST NOT be set on cloze questions. If present, it is ignored.

### 5.4 `build_sentence`

```
[question id=3 type="build_sentence" answer="Which store has the best deals?"]
**Context:** I need to buy a new laptop.
**Words:** has / the best / which / store / deals
[/question]
```

Rules:

- `answer` is the full target sentence. Trailing punctuation (`.`, `!`, `?`) is stripped before grading. Grading is case-insensitive.
- The `**Context:**` line (optional) is shown above the word bank. Its value is everything after the `**Context:**` marker on the same line (trimmed).
- The `**Words:**` line (required) lists the word chips, separated by `/`. Whitespace around each `/` is trimmed. A chip MAY contain internal spaces (`the best`). A chip MUST NOT contain a `/`.
- Duplicate chips are allowed; each is independent in the UI.
- The grading compares the student's constructed sentence against `answer` after: lowercasing both, removing `?`, `!`, `.` from both, and stripping leading/trailing whitespace. Any other difference (extra commas, missing words, different capitalization) is an incorrect answer.

### 5.5 `email` / `discussion`

Both are free-text writing tasks graded by a human rubric (0–5).

```
[question id=11 type="email" time_minutes=7]
**Scenario:** Your coworker recently recommended a nearby restaurant...

Write an email to Kevin. In your email, do the following:
- Explain what was wrong with the restaurant
- Describe the team's reaction to the visit

**To:** Kevin
**Subject:** Team Lunch Experience
[/question]
```

Rules:

- The entire content (after trimming) is rendered as Markdown for the student.
- `time_minutes` is advisory (shown as "Suggested time: N minutes"); it does NOT enforce a hard deadline.
- `answer` MUST NOT be set.
- Word counts are computed client-side and server-side by splitting on whitespace.

### 5.6 `listen_repeat` / `interview`

```
[audio src="3-09"]
[transcript]
Thank you for participating in our study today...
[/transcript]

[question id=9 type="interview" audio="3-09" time_seconds=30]
Listen to the question and give your response.
[/question]
```

Rules:

- The question MUST have access to an audio source, either via group-pending `[audio]` or its own `audio="…"` attribute. A question without audio will render an empty audio slot.
- `time_seconds` sets the recording timer. Default `30`. The recorder stops automatically when the timer expires.
- `answer` MUST NOT be set. These are scored by rubric (0–5).
- `listen_repeat` is a sentence-repetition task (5-pt rubric). `interview` is an extended-response task (5-pt rubric with different descriptors).

---

## 6. Markdown rendering

The following fields are rendered as Markdown at display time:

- `[passage]` content
- `[question]` content for `mc` (the prompt portion only), `email`, `discussion`, `listen_repeat`, `interview`
- `[explanation]` content
- The `**Context:**` value of a `build_sentence` question

The Markdown processor is Python-Markdown with the `tables` and `nl2br` extensions enabled. Relevant consequences:

- **Single newlines become `<br>`** (`nl2br`). A blank line ends a paragraph.
- GFM-style tables are supported.
- Fenced code blocks (```` ``` ````), inline code (`` ` ``), strong (`**`), emphasis (`*` or `_`), links (`[text](url)`), and lists (`-`, `*`, `1.`) all work.
- Raw HTML is allowed. Authors SHOULD avoid raw HTML unless necessary for the TOEFL format (e.g. `<sup>`, `<sub>`).
- `[passage]` content is later HTML-escaped when combined with teacher database content (see `helpers.md_html` usage); markdown is evaluated, **then** the result is trusted inline. Authors MUST NOT put user-supplied HTML into passages unless they trust it.

Special-meaning literals to avoid in Markdown content:

- Inside a cloze passage, `[N]` where `N` is an integer is interpreted as a blank. See §5.3.
- Four-space indentation at the start of a line will produce a code block in Markdown. Authors SHOULD avoid this unless intended.

---

## 7. Grading semantics

The system grades in two places:

1. **At end-of-module** (`/api/grade`) — used for instant feedback in practice mode and during section transitions.
2. **On final save** (`/api/save-results`) — the authoritative re-grade. This is the result shown in history/review.

Both implementations MUST agree. The following is the authoritative grading specification.

### 7.1 `mc`

- Correct ↔ `user_answer == question.answer` exactly (case-sensitive, so `B` == `B`, `b` != `B`).
- Unanswered ↔ incorrect.

### 7.2 `cloze`

Each blank is a separately-graded sub-question. For blank i:

- Expected fill = `full_answer[i]` with `prefix` stripped from front (case-insensitive) and `suffix` stripped from back (case-insensitive), then lowercased.
- User input = the N characters the student typed, lowercased and trimmed.
- Correct ↔ `user_input == expected_fill`.

A cloze question of N blanks contributes N to the module total and the count of correct fills to the module correct count. The question as a whole is "fully correct" only when all N blanks are correct.

### 7.3 `build_sentence`

- Normalize: lowercase, strip `[?!.]`, trim whitespace.
- Correct ↔ `normalized(user) == normalized(answer)`.

### 7.4 `email` / `discussion` / `listen_repeat` / `interview`

Not auto-graded. The system records:

- For `email`/`discussion`: the full text and a word count.
- For `listen_repeat`/`interview`: a flag indicating whether audio was recorded.

A teacher assigns a 0–5 rubric score per question. Rubric scores start as **drafts** (invisible to students) and become visible when a teacher explicitly publishes them via the "Publish Scores" button in Review.

### 7.5 Band scoring

Raw correct/total per section is converted to a 1.0–6.0 band score using the tables in `helpers.py`:

- **Reading / Listening**: score out of 30 → band via `_RL_BAND_TABLE`.
- **Writing**: (auto-graded writing questions + 5× each rubric score) out of (auto + 5× rubric count) → 20-point scale → band via `_WRITING_BAND_TABLE`.
- **Speaking**: 5× rubric sum out of 5× rubric count → 55-point scale → band via `_SPEAKING_BAND_TABLE`.

Overall band = average of section bands, rounded to nearest 0.5.

A section whose rubric-scored questions have unsubmitted scores is flagged `needs_rubric=true` and the overall band is shown as "awaiting score" to the student.

---

## 8. TTS companion script

For every `.md` file that declares one or more `[audio]` sources, the author MUST produce a companion `.tts` file with the same basename. This file drives the batch audio-synthesis pipeline.

Example correspondence:

```
pt1-listening-m1.md   →   pt1-listening-m1.tts
pt1-speaking-m1.md    →   pt1-speaking-m1.tts
practice-test-1.md    →   practice-test-1.tts   (if it contains audio)
```

### 8.1 Line-based grammar

A `.tts` file is line-oriented. Each significant line begins with `@@`. All other lines are **segment text** belonging to the currently-open `@@SEGMENT_BEGIN`.

```
file        ::= (blank-line | tts-block)+
tts-block   ::= "@@TTS_FILE_BEGIN" SP kv-pairs NL
                (blank-line | segment-block | pause-line)+
                ("@@FFMPEG_CONCAT" SP kv-pairs NL)?
                "@@TTS_FILE_END" NL
segment-block ::= "@@SEGMENT_BEGIN" SP kv-pairs NL
                  text-line+
                  "@@SEGMENT_END" NL
pause-line  ::= "@@PAUSE" SP "seconds=" integer NL
kv-pairs    ::= kv (SP kv)*
kv          ::= identifier "=" (quoted | bareword)
text-line   ::= <any UTF-8 line NOT beginning with "@@">
```

`@@` directives MUST begin in column 1. Trailing whitespace is ignored on directive lines.

### 8.2 `@@TTS_FILE_BEGIN`

Required attributes:

| Attribute | Type   | Description |
|-----------|--------|-------------|
| `id`      | string | Logical audio id. MUST equal the `src` value of the matching `[audio src="…"]` tag in the `.md` file. Matches `[A-Za-z0-9._-]+`. |
| `output`  | string | Final concatenated filename. Convention: `<id>.ogg`. |

There MUST be exactly one `@@TTS_FILE_BEGIN` ... `@@TTS_FILE_END` block per unique `id`.

### 8.3 `@@SEGMENT_BEGIN` / `@@SEGMENT_END`

Required attributes on `@@SEGMENT_BEGIN`:

| Attribute      | Type   | Description |
|----------------|--------|-------------|
| `speaker`      | enum   | Exactly `male` or `female`. Selects the TTS voice. |
| `segment_file` | string | Filename to write this segment's audio to. MUST match `[A-Za-z0-9._/-]+` and end in `.ogg`. |

Content rules:

- Every line between `@@SEGMENT_BEGIN` and `@@SEGMENT_END` is the spoken text, concatenated with spaces between lines (not newlines — TTS engines handle prosody off punctuation).
- Do NOT include speaker labels like `Woman:` or `Man:` in the text. Only the spoken words.
- Do NOT include stage directions (`[pauses]`, `[laughing]`, `[2-second pause]`). For explicit pauses, see §8.4.
- Empty segments are invalid.
- If the text contains `@@` at the start of a line, that line MUST be moved inside a new segment or prefixed with a non-`@@` character (e.g. leading space) — but doing so alters the TTS input. Authors MUST avoid `@@` at line starts in text.

### 8.4 `@@PAUSE`

Format: `@@PAUSE seconds=<positive integer>`

A pause line MAY appear between two `@@SEGMENT_END` lines (i.e. between segments) and introduces a silent span of the given number of seconds in the final concatenation. Pauses MUST NOT appear inside a `@@SEGMENT_BEGIN/END` block.

### 8.5 `@@FFMPEG_CONCAT`

Required when the block contains more than one segment, or any `@@PAUSE` lines. Required attributes:

| Attribute  | Type   | Description |
|------------|--------|-------------|
| `segments` | string | Comma-separated, ordered list of segment filenames (and any pause filenames produced by the build script). No whitespace inside the list. |
| `output`   | string | Final filename. MUST equal the `output` on `@@TTS_FILE_BEGIN`. |

The order of entries in `segments` is the order they are concatenated. For a single-segment block, `@@FFMPEG_CONCAT` MAY be omitted; the single segment's `segment_file` MUST then equal the `output` on `@@TTS_FILE_BEGIN`.

### 8.6 Worked examples

Single-speaker, single-segment (no concat needed):

```
@@TTS_FILE_BEGIN id="1-01" output="1-01.ogg"
@@SEGMENT_BEGIN speaker="female" segment_file="1-01.ogg"
Do you know if the campus bookstore is still open?
@@SEGMENT_END
@@TTS_FILE_END
```

Multi-speaker conversation:

```
@@TTS_FILE_BEGIN id="1-09" output="1-09.ogg"
@@SEGMENT_BEGIN speaker="female" segment_file="1-09-a.ogg"
Hey Mark, did you finish the research paper for Professor Chen's class?
@@SEGMENT_END
@@SEGMENT_BEGIN speaker="male" segment_file="1-09-b.ogg"
Not yet. I'm having trouble finding reliable sources.
@@SEGMENT_END
@@FFMPEG_CONCAT segments="1-09-a.ogg,1-09-b.ogg" output="1-09.ogg"
@@TTS_FILE_END
```

With an explicit pause:

```
@@TTS_FILE_BEGIN id="1-10" output="1-10.ogg"
@@SEGMENT_BEGIN speaker="female" segment_file="1-10-a.ogg"
Please answer the following question.
@@SEGMENT_END
@@PAUSE seconds=2
@@SEGMENT_BEGIN speaker="male" segment_file="1-10-b.ogg"
What is your favorite subject and why?
@@SEGMENT_END
@@FFMPEG_CONCAT segments="1-10-a.ogg,pause_2s.ogg,1-10-b.ogg" output="1-10.ogg"
@@TTS_FILE_END
```

The build script is responsible for generating `pause_Ns.ogg` silent files on demand; the `segments` list names them explicitly so the script can concatenate without ambiguity.

---

## 9. Parser behavior (normative) for edge cases

An AI agent producing test files MUST NOT rely on any of the following parser leniencies; they are documented here only so that agents reading malformed files can predict what the runtime will do.

| Situation | Parser behavior |
|-----------|-----------------|
| YAML front matter missing | Parse fails; file is skipped from the catalog with a warning. |
| `test_id` missing | Parse fails. |
| Body with no `[module]` blocks | Parse fails. |
| `[group]` outside a `[module]` | The group and its questions are discarded. |
| `[question]` outside a `[group]` | The question is discarded. |
| `[passage]` outside a `[group]` | The passage is discarded. |
| Duplicate `[passage id="…"]` in a group | The later one overwrites the earlier. |
| `passage="x"` where `x` is not defined in the same group | The question renders without a passage. |
| `audio="x"` where `x.ogg` does not exist on disk | The question renders with a broken audio player; 404 on play. |
| Question `type` not in §5.1 | Treated as `mc` with no choices. |
| `mc` missing `answer` | Graded as incorrect for all inputs. |
| `mc` `answer="Z"` (non-A–D) | Graded as incorrect for all inputs. |
| `cloze` passage with more `[N]` than answers | Extra blanks grade as always-incorrect. |
| `cloze` passage with fewer `[N]` than answers | Extra answers are silently dropped. |
| `build_sentence` missing `answer` | Graded as incorrect for all inputs. |
| `[explanation]` before any question in the group | Silently discarded. |
| Multiple `[explanation]` for one question | Only the last is kept. |
| Two `[/module]` tags | The outer module closes at the first `[/module]`; content after is discarded until the next `[module]`. |
| Nested `[module]` | Not supported. The inner `[module` tag is treated as literal text inside the outer block's content (usually causing downstream parse issues). |
| Attribute with duplicate key (`id=1 id=2`) | The later value wins. |
| Attribute value containing unquoted space | Parse is best-effort; use quoted form. |

---

## 10. Authoring checklist (for the AI agent)

Before declaring a test file complete, verify all of the following:

1. YAML front matter present with `test_id` and `test_name`. `test_id` matches `[A-Za-z0-9._-]+`.
2. Every `[module]` declares `section` (one of four lowercase values), `module` (integer), `timer_minutes` (integer).
3. Every `[group]` has a `title` attribute.
4. Every `[question]` has a unique integer `id` within its module.
5. Every `[question]` has a valid `type`.
6. Every `mc` question has `answer="A|B|C|D"` (uppercase) and exactly four `(A)` through `(D)` choice lines.
7. Every `cloze` question's passage has the same number of `[N]` markers as the answer list has entries, and each `[N]` value equals `len(answer_word) - len(prefix) - len(suffix)`.
8. Every `build_sentence` question has an `answer` and a `**Words:**` line with `/`-separated chips.
9. Every `email`/`discussion` question has `time_minutes` (recommended) and a descriptive prompt.
10. Every `listen_repeat`/`interview` question has audio (via pending `[audio]` or its own `audio=`) and `time_seconds`.
11. Every `[audio src="X"]` has a matching `X.ogg` file in the audio folder (or a matching `@@TTS_FILE_BEGIN id="X"` in the companion `.tts` script).
12. Every `@@TTS_FILE_BEGIN` id matches an `[audio src=…]` in the `.md` file.
13. Every `@@SEGMENT_BEGIN` has `speaker="male"` or `speaker="female"` and a `segment_file` ending in `.ogg`.
14. Multi-segment TTS blocks end with `@@FFMPEG_CONCAT`.
15. No `@@` at the start of any line of spoken text.

---

## Appendix A. Reserved future attributes

The parser currently ignores unknown attributes. The following names are reserved for future use; authors SHOULD NOT use them on their own:

- `module.mode`, `module.pass_threshold`
- `question.points`, `question.tags`, `question.difficulty`
- `passage.title`
- `audio.autoplay`, `audio.replay`

## Appendix B. Reserved future question types

- `matching` — match items in two columns
- `order` — reorder a list into the correct sequence
- `highlight` — click a word or phrase in a passage
- `dictation` — type exactly what is heard

These types will follow the same `[question type="…"]` pattern but with type-specific content grammars to be defined.
