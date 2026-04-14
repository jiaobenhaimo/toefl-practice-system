"""
parser.py - Parses TOEFL practice test markdown files.
Only supports multi-module format: [module] blocks in body.
"""

import re
import yaml
from pathlib import Path

SECTION_ORDER = {'reading': 0, 'listening': 1, 'writing': 2, 'speaking': 3}

# Pre-compiled regex patterns (avoids recompilation on every line/call)
_RE_YAML_HEADER = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.DOTALL)
_RE_BLOCK_ATTRS = re.compile(r'(\w+)=(?:"([^"]*?)"|([^\s\]]+))')
_RE_MC_CHOICE = re.compile(r'\(([A-D])\)\s*(.+)')
_RE_MC_PROMPT_LINE = re.compile(r'\([A-D]\)')
_RE_CLOZE_ANSWER = re.compile(r'\d+\.\s*(.+)')
_RE_CONTEXT = re.compile(r'\*\*Context:\*\*\s*(.+)')
_RE_WORDS = re.compile(r'\*\*Words:\*\*\s*(.+)')
_RE_GROUP_OPEN = re.compile(r'\[group\s+title="(.+?)"\]')
_RE_AUDIO = re.compile(r'\[audio\s+src="(.+?)"\]')
_RE_BLOCK_OPEN = re.compile(r'\[(passage|transcript|question|explanation)\s*(.*?)\]$')
_RE_BLOCK_CLOSE = re.compile(r'\[/(passage|transcript|question|explanation)\]')
_RE_MODULE = re.compile(r'\[module\s+(.*?)\](.*?)\[/module\]', re.DOTALL)
_RE_MODULE_ATTRS = re.compile(r'\[module\s+(.*?)\]')
_RE_CLOZE_BLANK = re.compile(r'(\w*)\[(\d+)\](\w*)')


def parse_yaml_header(text):
    match = _RE_YAML_HEADER.match(text)
    if not match:
        raise ValueError("No YAML front matter found")
    header = yaml.safe_load(match.group(1))
    body = text[match.end():]
    return header, body


def parse_block_attrs(tag_line):
    attrs = {}
    for m in _RE_BLOCK_ATTRS.finditer(tag_line):
        attrs[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
    return attrs


def parse_mc_choices(content):
    choices = {}
    for m in _RE_MC_CHOICE.finditer(content):
        choices[m.group(1)] = m.group(2).strip()
    return choices


def parse_mc_prompt(content):
    lines = []
    for line in content.split('\n'):
        if _RE_MC_PROMPT_LINE.match(line.strip()):
            break
        lines.append(line)
    return '\n'.join(lines).strip()


def parse_build_sentence(content):
    context = ''
    words = []
    for line in content.split('\n'):
        stripped = line.strip()
        cm = _RE_CONTEXT.match(stripped)
        if cm:
            context = cm.group(1).strip()
            if words:
                break
            continue
        wm = _RE_WORDS.match(stripped)
        if wm:
            words = [w.strip() for w in wm.group(1).split('/')]
            if context:
                break
    return {'context': context, 'words': words}


def parse_groups(lines_text):
    """Parse text into groups containing passages, audio, transcripts, questions."""
    groups = []
    current_group = None
    current_block_type = None
    current_block_attrs = None
    current_block_lines = []

    def flush_block():
        nonlocal current_block_type, current_block_attrs, current_block_lines
        if current_block_type and current_group is not None:
            content = '\n'.join(current_block_lines).strip()
            if current_block_type == 'passage':
                pid = current_block_attrs.get('id', 'p' + str(len(current_group['passages'])))
                current_group['passages'][pid] = content
            elif current_block_type == 'transcript':
                current_group['items'].append({'type': 'transcript', 'content': content})
            elif current_block_type == 'question':
                q = dict(current_block_attrs)
                qtype = q.pop('type', 'mc')
                q['type_'] = qtype
                q['content'] = content
                if qtype == 'cloze':
                    q['cloze_answers'] = [
                        m.group(1).strip()
                        for m in _RE_CLOZE_ANSWER.finditer(content)
                    ]
                elif qtype == 'mc':
                    q['choices'] = parse_mc_choices(content)
                    q['prompt'] = parse_mc_prompt(content)
                elif qtype == 'build_sentence':
                    q['details'] = parse_build_sentence(content)
                current_group['items'].append({'type': 'question', 'data': q})
            elif current_block_type == 'explanation':
                # Attach explanation to the most recent question
                for item in reversed(current_group['items']):
                    if item['type'] == 'question':
                        item['data']['explanation'] = content
                        break
        current_block_type = None
        current_block_attrs = None
        current_block_lines = []

    for line in lines_text.split('\n'):
        stripped = line.strip()

        if not stripped:
            if current_block_type is not None:
                current_block_lines.append(line)
            continue

        # Fast path: content inside a block (most lines)
        if current_block_type is not None:
            if len(stripped) > 1 and stripped[0] == '[' and stripped[1] == '/':
                cm = _RE_BLOCK_CLOSE.match(stripped)
                if cm:
                    flush_block()
                    continue
            elif stripped[0] != '[':
                current_block_lines.append(line)
                continue
            else:
                current_block_lines.append(line)
                continue

        # Outside a block — check for structural tags
        if stripped[0] == '[':
            if stripped == '[/group]':
                flush_block()
                if current_group is not None:
                    groups.append(current_group)
                    current_group = None
                continue

            gm = _RE_GROUP_OPEN.match(stripped)
            if gm:
                if current_group is not None:
                    flush_block()
                    groups.append(current_group)
                current_group = {'title': gm.group(1), 'passages': {}, 'items': []}
                continue

            am = _RE_AUDIO.match(stripped)
            if am and current_group is not None:
                flush_block()
                current_group['items'].append({'type': 'audio', 'src': am.group(1)})
                continue

            bm = _RE_BLOCK_OPEN.match(stripped)
            if bm and not stripped.startswith('[/'):
                flush_block()
                current_block_type = bm.group(1)
                current_block_attrs = parse_block_attrs(stripped)
                current_block_lines = []
                continue

    flush_block()
    if current_group is not None:
        groups.append(current_group)

    return groups


def parse_test_file(filepath):
    """
    Parse a markdown test file. Returns modules with full question data.
    YAML header: test_id, test_name.
    Body: [module section="..." module=N timer_minutes=N] ... [/module] blocks.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        text = f.read()
    header, body = parse_yaml_header(text)
    stem = Path(filepath).stem
    audio_dir = str(Path(filepath).parent / stem)

    modules = []
    for match in _RE_MODULE.finditer(body):
        attrs = parse_block_attrs('[module ' + match.group(1) + ']')
        groups = parse_groups(match.group(2))
        modules.append({
            'section': attrs.get('section', ''),
            'module': int(attrs.get('module', 1)),
            'timer_minutes': int(attrs.get('timer_minutes', 0)),
            'groups': groups,
        })

    modules.sort(key=lambda m: (SECTION_ORDER.get(m['section'], 99), m['module']))

    if not modules:
        raise ValueError(f"No [module] blocks found in {filepath}")

    return {
        'header': header,
        'modules': modules,
        'audio_dir': audio_dir,
        'filepath': str(filepath),
    }


def scan_test_headers(filepath):
    """
    Lightweight scan: read only YAML header + [module] attribute lines.
    Skips full body parsing — much faster for catalog listing.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        text = f.read()
    header, body = parse_yaml_header(text)

    modules = []
    for match in _RE_MODULE_ATTRS.finditer(body):
        attrs = parse_block_attrs(match.group(0))
        modules.append({
            'section': attrs.get('section', ''),
            'module': int(attrs.get('module', 1)),
            'timer_minutes': int(attrs.get('timer_minutes', 0)),
        })

    modules.sort(key=lambda m: (SECTION_ORDER.get(m['section'], 99), m['module']))
    return header, modules


def scan_tests_directory(tests_dir):
    """Scan tests directory. Returns dict of tests grouped by test_id."""
    tests = {}
    tests_path = Path(tests_dir)
    if not tests_path.exists():
        return tests

    for md_file in sorted(tests_path.glob('*.md')):
        if md_file.name.startswith('README'):
            continue
        try:
            header, modules = scan_test_headers(str(md_file))
            test_id = header.get('test_id', '')

            if test_id not in tests:
                tests[test_id] = {
                    'test_id': test_id,
                    'test_name': header.get('test_name', test_id),
                    'modules': []
                }

            for idx, mod in enumerate(modules):
                tests[test_id]['modules'].append({
                    'section': mod['section'],
                    'module': mod['module'],
                    'timer_minutes': mod['timer_minutes'],
                    'filename': md_file.name,
                    'module_index': idx,
                })
        except Exception as e:
            print(f"Warning: Could not parse {md_file}: {e}")

    for tid in tests:
        tests[tid]['modules'].sort(
            key=lambda m: (SECTION_ORDER.get(m['section'], 99), m['module'])
        )
    return tests


def build_question_list(module_data):
    """Flatten a single module into ordered list of pages for the test UI."""
    pages = []
    section = module_data['section']

    for group in module_data['groups']:
        passages = group['passages']
        pending_audio = None

        for item in group['items']:
            itype = item['type']
            if itype == 'audio':
                pending_audio = item['src']
                continue
            if itype == 'transcript':
                continue
            if itype != 'question':
                continue

            q = item['data']
            qtype = q.get('type_', 'mc')
            page = {
                'group_title': group['title'],
                'question_id': q.get('id', ''),
                'question_type': qtype,
                'section': section,
            }

            passage_id = q.get('passage', '')
            if passage_id and passage_id in passages:
                page['passage'] = passages[passage_id]
                page['passage_id'] = passage_id

            audio_src = q.get('audio') or pending_audio or ''
            if audio_src:
                page['audio'] = audio_src

            if qtype == 'mc':
                page['prompt'] = q.get('prompt', '')
                page['choices'] = q.get('choices', {})
                page['answer'] = q.get('answer', '')
            elif qtype == 'cloze':
                full_answers = q.get('cloze_answers', [])
                page['cloze_answers'] = full_answers
                page['answer'] = full_answers
                # Compute expected fills by extracting [N] blanks from passage
                passage_text = page.get('passage', '')
                blanks = _RE_CLOZE_BLANK.findall(passage_text)
                cloze_fills = []
                for i, (prefix, count_str, suffix) in enumerate(blanks):
                    if i < len(full_answers):
                        full_word = full_answers[i].strip().lower()
                        p = prefix.lower()
                        s = suffix.lower()
                        fill = full_word
                        if p and full_word.startswith(p):
                            fill = fill[len(p):]
                        if s and fill.endswith(s):
                            fill = fill[:-len(s)]
                        cloze_fills.append(fill)
                    else:
                        cloze_fills.append('')
                page['cloze_fills'] = cloze_fills
            elif qtype == 'build_sentence':
                page['details'] = q.get('details', {})
                page['answer'] = q.get('answer', '')
            elif qtype in ('email', 'discussion'):
                page['content'] = q.get('content', '')
                page['time_minutes'] = q.get('time_minutes', '')
            elif qtype in ('listen_repeat', 'interview'):
                page['content'] = q.get('content', '')
                page['time_seconds'] = int(q.get('time_seconds', 30))

            # Explanation (from [explanation] block in markdown)
            if q.get('explanation'):
                page['explanation'] = q['explanation']

            pages.append(page)
            # pending_audio persists until the next [audio] block resets it

    return pages
