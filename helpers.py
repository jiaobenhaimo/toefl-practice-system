"""
helpers.py — Shared helpers, decorators, caching, and config for the TOEFL Practice System.
Imported by app.py and all route blueprints.
"""
import os, json, copy, time, re, markdown, yaml
from pathlib import Path
from functools import wraps
from flask import session, request, redirect, abort, g
from parser import scan_tests_directory, parse_test_file
import database as db

# ===== Config =====

_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.yaml')
with open(_config_path) as f:
    SITE_CONFIG = yaml.safe_load(f)

TESTS_DIR = os.environ.get('TOEFL_TESTS_DIR',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tests'))

RECORDINGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'recordings')

# ===== Caching =====
# Per-process caches. Under gunicorn with multiple workers, each worker
# maintains its own cache. Changes propagate within one request per worker.

_parse_cache = {}
_scan_cache = {'mtime': 0, 'count': -1, 'result': None}


def cached_parse(filepath):
    """Parse a test file with mtime-based caching."""
    mtime = os.path.getmtime(filepath)
    c = _parse_cache.get(filepath)
    if c and c[0] == mtime:
        return c[1]
    r = parse_test_file(filepath)
    _parse_cache[filepath] = (mtime, r)
    return r


def cached_scan():
    """Scan the tests directory with mtime-based caching."""
    p = Path(TESTS_DIR)
    if not p.exists():
        return {}
    latest, cnt = 0, 0
    for f in p.glob('*.md'):
        cnt += 1
        mt = f.stat().st_mtime
        if mt > latest:
            latest = mt
    if (_scan_cache['result'] is not None
            and _scan_cache['mtime'] >= latest
            and _scan_cache['count'] == cnt):
        return _scan_cache['result']
    r = scan_tests_directory(TESTS_DIR)
    _scan_cache.update(mtime=latest, count=cnt, result=r)
    return r


# ===== Markdown =====

def md_html(text):
    """Thread-safe markdown-to-HTML conversion."""
    md = markdown.Markdown(extensions=['tables', 'nl2br'])
    return md.convert(text)


def pages_to_html(pages):
    """Convert markdown fields to HTML. Deep-copies to avoid mutating cache."""
    result = []
    for p in pages:
        cp = copy.deepcopy(p)
        for k in ('passage', 'prompt', 'content'):
            if k in cp:
                cp[k + '_html'] = md_html(cp[k])
        if 'details' in cp and 'context' in cp['details']:
            cp['details']['context_html'] = md_html(cp['details']['context'])
        result.append(cp)
    return result


# ===== Path safety =====

def safe_path(base, user_path):
    """Resolve a user-provided path under a base directory. Returns None if escapes."""
    b = os.path.realpath(base)
    t = os.path.realpath(os.path.join(base, user_path))
    return t if t.startswith(b + os.sep) or t == b else None


# ===== Auth helpers =====

def cur_user():
    """Get the current logged-in user, cached per-request in flask.g."""
    if not hasattr(g, '_cur_user'):
        uid = session.get('user_id')
        g._cur_user = db.get_user(uid) if uid else None
    return g._cur_user


def require_login(f):
    """Decorator: redirect to login if not authenticated."""
    @wraps(f)
    def dec(*a, **kw):
        if not session.get('user_id'):
            return redirect('/login?next=' + request.path)
        return f(*a, **kw)
    return dec


def require_role(*roles):
    """Decorator: abort 403 if user role not in allowed list."""
    def deco(f):
        @wraps(f)
        def dec(*a, **kw):
            u = cur_user()
            if not u or u['role'] not in roles:
                abort(403)
            return f(*a, **kw)
        return dec
    return deco


# ===== JSON helpers =====

def parse_json(s, default=None):
    """Safely parse a JSON string, returning default on failure."""
    if not s:
        return default if default is not None else []
    try:
        return json.loads(s)
    except Exception:
        return default if default is not None else []


def get_result_or_403(result_id):
    """Get a result by ID with permission check. Aborts on failure."""
    u = cur_user()
    r = db.get_result_by_id(result_id)
    if not r:
        abort(404)
    if u['role'] == 'student' and r['user_id'] != u['id']:
        abort(403)
    if u['role'] == 'parent':
        student = db.get_linked_student(u['id'])
        if not student or r['user_id'] != student['id']:
            abort(403)
    return r


# ===== Date formatting =====

_MONTHS_FULL = ['January', 'February', 'March', 'April', 'May', 'June',
                'July', 'August', 'September', 'October', 'November', 'December']
_MONTHS_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def fmtdate(datestr):
    """Format ISO date as 'Apr 14, 2026'."""
    if not datestr or len(datestr) < 10:
        return datestr or ''
    try:
        y, m, d = int(datestr[:4]), int(datestr[5:7]), int(datestr[8:10])
        return f'{_MONTHS_ABBR[m-1]} {d}, {y}'
    except Exception:
        return datestr[:10]


def fmtdate_full(datestr):
    """Format ISO date as 'April 14, 2026'."""
    if not datestr or len(datestr) < 10:
        return datestr or ''
    try:
        y, m, d = int(datestr[:4]), int(datestr[5:7]), int(datestr[8:10])
        return f'{_MONTHS_FULL[m-1]} {d}, {y}'
    except Exception:
        return datestr[:10]


# ===== Login rate limiting =====

_login_attempts = {}
_RATE_LIMIT_WINDOW = 300  # 5 minutes
_RATE_LIMIT_MAX = 10


def check_rate_limit(ip):
    """Returns True if rate limited."""
    now = time.time()
    if len(_login_attempts) > 1000:
        stale = [k for k, v in _login_attempts.items()
                 if not v or now - v[-1] > _RATE_LIMIT_WINDOW]
        for k in stale:
            del _login_attempts[k]
    attempts = _login_attempts.get(ip, [])
    attempts = [t for t in attempts if now - t < _RATE_LIMIT_WINDOW]
    _login_attempts[ip] = attempts
    return len(attempts) >= _RATE_LIMIT_MAX


def record_attempt(ip):
    _login_attempts.setdefault(ip, []).append(time.time())


# ===== TOEFL 2026 Scoring =====

_SPEAKING_BAND_TABLE = [
    (52, 55, 6.0), (47, 51, 5.5), (42, 46, 5.0), (37, 41, 4.5),
    (32, 36, 4.0), (27, 31, 3.5), (22, 26, 3.0), (17, 21, 2.5),
    (12, 16, 2.0), (7, 11, 1.5), (0, 6, 1.0),
]

_WRITING_BAND_TABLE = [
    (19, 20, 6.0), (17, 18, 5.5), (15, 16, 5.0), (13, 14, 4.5),
    (11, 12, 4.0), (9, 10, 3.5), (7, 8, 3.0), (5, 6, 2.5),
    (3, 4, 2.0), (2, 2, 1.5), (0, 1, 1.0),
]

_RL_BAND_TABLE = [
    (29, 30, 6.0), (27, 28, 5.5), (24, 26, 5.0), (22, 23, 4.5),
    (18, 21, 4.0), (12, 17, 3.5), (6, 11, 3.0), (4, 5, 2.5),
    (3, 3, 2.0), (2, 2, 1.5), (0, 1, 1.0),
]


def lookup_band(table, raw):
    raw = max(0, round(raw))
    for lo, hi, band in table:
        if lo <= raw <= hi:
            return band
    return 1.0


def section_band(section, details, rubric_map):
    """Calculate TOEFL 2026 1-6 band for a section."""
    if section in ('reading', 'listening'):
        correct = sum(1 for d in details if d.get('correct') is True)
        total = sum(1 for d in details if d.get('correct') is not None)
        if total == 0:
            return None
        return lookup_band(_RL_BAND_TABLE, correct / total * 30)
    elif section == 'writing':
        raw, max_raw = 0, 0
        for d in details:
            dt = d.get('type', '')
            if dt in ('build_sentence', 'mc', 'cloze') and d.get('correct') is not None:
                max_raw += 1
                if d.get('correct'):
                    raw += 1
        for d in details:
            dt, qid = d.get('type', ''), str(d.get('qid', ''))
            if dt in ('email', 'discussion'):
                max_raw += 5
                if qid in rubric_map:
                    raw += rubric_map[qid]
        if max_raw == 0:
            return None
        return lookup_band(_WRITING_BAND_TABLE, raw / max_raw * 20)
    elif section == 'speaking':
        raw, max_raw = 0, 0
        for d in details:
            dt, qid = d.get('type', ''), str(d.get('qid', ''))
            if dt in ('listen_repeat', 'interview'):
                max_raw += 5
                if qid in rubric_map:
                    raw += rubric_map[qid]
        if max_raw == 0:
            return None
        return lookup_band(_SPEAKING_BAND_TABLE, raw / max_raw * 55)
    return None
