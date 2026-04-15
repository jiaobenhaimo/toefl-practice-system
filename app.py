"""
app.py — Flask application for TOEFL Practice Test System.
Auth, admin, teacher dashboards, server-side grading.
"""
import os, json, secrets, re, markdown, yaml, time, copy, shutil
from functools import wraps
from flask import (
    Flask, render_template, jsonify, request, session,
    send_from_directory, abort, redirect, url_for, flash, send_file
)
from parser import scan_tests_directory, parse_test_file, build_question_list
import database as db

# Load config
_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.yaml')
with open(_config_path) as f:
    SITE_CONFIG = yaml.safe_load(f)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['PERMANENT_SESSION_LIFETIME'] = 60 * 60 * 24 * 31
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB upload limit (#13)

@app.context_processor
def inject_config():
    """Make site config and announcement available in all templates."""
    announcement = db.get_active_announcement()
    return {'site': SITE_CONFIG.get('site', {}), 'config': SITE_CONFIG, 'announcement': announcement}

# Date formatting filters
_MONTHS_FULL = ['January','February','March','April','May','June','July','August','September','October','November','December']
_MONTHS_ABBR = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']

@app.template_filter('fmtdate')
def filter_fmtdate(datestr):
    """Format ISO date as 'Apr 14, 2026' (abbreviated month for tables)."""
    if not datestr or len(datestr) < 10: return datestr or ''
    try:
        y, m, d = int(datestr[:4]), int(datestr[5:7]), int(datestr[8:10])
        return f'{_MONTHS_ABBR[m-1]} {d}, {y}'
    except Exception: return datestr[:10]

@app.template_filter('fmtdate_full')
def filter_fmtdate_full(datestr):
    """Format ISO date as 'April 14, 2026' (full month for display)."""
    if not datestr or len(datestr) < 10: return datestr or ''
    try:
        y, m, d = int(datestr[:4]), int(datestr[5:7]), int(datestr[8:10])
        return f'{_MONTHS_FULL[m-1]} {d}, {y}'
    except Exception: return datestr[:10]

TESTS_DIR = os.environ.get('TOEFL_TESTS_DIR',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tests'))

# NOTE: _parse_cache and _scan_cache are per-process. Under gunicorn with
# multiple workers, each worker maintains its own cache. This means file
# changes may take up to one request per worker to propagate. For single-
# process deployments (dev server, gunicorn -w 1) this is fine. (#11)
_parse_cache = {}
_scan_cache = {'mtime': 0, 'count': -1, 'result': None}

# ===== CSRF Protection (#1) =====
def _csrf_token():
    """Get or create a CSRF token for the current session."""
    if '_csrf' not in session:
        session['_csrf'] = secrets.token_hex(16)
    return session['_csrf']

@app.context_processor
def inject_csrf():
    return {'csrf_token': _csrf_token}

@app.before_request
def csrf_check():
    """Validate CSRF token on all POST requests from forms (not JSON APIs)."""
    if request.method == 'POST' and request.content_type != 'application/json':
        token = request.form.get('_csrf', '')
        if not token or token != session.get('_csrf'):
            abort(403)

# ===== Login Rate Limiting (#2) =====
_login_attempts = {}  # {ip: [(timestamp, ...)] }
_RATE_LIMIT_WINDOW = 300  # 5 minutes
_RATE_LIMIT_MAX = 10  # max attempts per window

def _check_rate_limit(ip):
    """Returns True if rate limited. Also prunes stale IPs."""
    now = time.time()
    # Prune stale entries every 100 checks to bound memory
    if len(_login_attempts) > 1000:
        stale = [k for k, v in _login_attempts.items() if not v or now - v[-1] > _RATE_LIMIT_WINDOW]
        for k in stale:
            del _login_attempts[k]
    attempts = _login_attempts.get(ip, [])
    attempts = [t for t in attempts if now - t < _RATE_LIMIT_WINDOW]
    _login_attempts[ip] = attempts
    return len(attempts) >= _RATE_LIMIT_MAX

def _record_attempt(ip):
    _login_attempts.setdefault(ip, []).append(time.time())

# ===== Helpers =====

def _cached_parse(filepath):
    mtime = os.path.getmtime(filepath)
    c = _parse_cache.get(filepath)
    if c and c[0] == mtime: return c[1]
    r = parse_test_file(filepath)
    _parse_cache[filepath] = (mtime, r)
    return r

def _cached_scan():
    from pathlib import Path
    p = Path(TESTS_DIR)
    if not p.exists(): return {}
    latest, cnt = 0, 0
    for f in p.glob('*.md'):
        cnt += 1
        mt = f.stat().st_mtime
        if mt > latest: latest = mt
    if _scan_cache['result'] is not None and _scan_cache['mtime'] >= latest and _scan_cache['count'] == cnt:
        return _scan_cache['result']
    r = scan_tests_directory(TESTS_DIR)
    _scan_cache.update(mtime=latest, count=cnt, result=r)
    return r

def md_html(text):
    """Thread-safe markdown conversion (#3)."""
    md = markdown.Markdown(extensions=['tables', 'nl2br'])
    return md.convert(text)

def pages_to_html(pages):
    """Convert markdown fields to HTML. Deep-copies to avoid mutating cache (#10)."""
    result = []
    for p in pages:
        cp = copy.deepcopy(p)
        for k in ('passage','prompt','content'):
            if k in cp: cp[k+'_html'] = md_html(cp[k])
        if 'details' in cp and 'context' in cp['details']:
            cp['details']['context_html'] = md_html(cp['details']['context'])
        result.append(cp)
    return result

def safe_path(base, user_path):
    b = os.path.realpath(base)
    t = os.path.realpath(os.path.join(base, user_path))
    return t if t.startswith(b + os.sep) or t == b else None

def cur_user():
    uid = session.get('user_id')
    return db.get_user(uid) if uid else None

# ===== Auth decorators =====

def require_login(f):
    @wraps(f)
    def dec(*a, **kw):
        if not session.get('user_id'):
            return redirect(url_for('login', next=request.url))
        return f(*a, **kw)
    return dec

def require_role(*roles):
    def deco(f):
        @wraps(f)
        def dec(*a, **kw):
            u = cur_user()
            if not u or u['role'] not in roles: abort(403)
            return f(*a, **kw)
        return dec
    return deco

# ===== Auth routes =====

@app.route('/login', methods=['GET','POST'])
def login():
    if session.get('user_id'): return redirect('/')
    error = None
    if request.method == 'POST':
        ip = request.remote_addr or '0.0.0.0'
        if _check_rate_limit(ip):
            error = 'Too many login attempts. Please wait a few minutes.'
            return render_template('login.html', error=error)
        _record_attempt(ip)
        u = db.authenticate(request.form.get('username','').strip(), request.form.get('password',''))
        if u:
            # Regenerate session to prevent fixation (#9)
            remember = request.form.get('remember') == 'on'
            session.clear()
            session['user_id'] = u['id']
            session['role'] = u['role']
            session['display_name'] = u['display_name']
            if remember: session.permanent = True
            nxt = request.args.get('next', '/')
            # Prevent open redirect — only allow relative paths
            if not nxt.startswith('/') or nxt.startswith('//'):
                nxt = '/'
            return redirect(nxt)
        error = 'Invalid username or password'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear(); return redirect('/login')

@app.route('/guest')
def guest_mode():
    session['guest'] = True; session.pop('user_id', None)
    return redirect('/catalog')

# ===== Main routes =====

@app.route('/')
def index():
    u = cur_user(); g = session.get('guest', False)
    if not u and not g: return redirect('/login')
    if u and u['role'] == 'student': return redirect('/assignments')
    return redirect('/catalog')

@app.route('/catalog')
def catalog():
    u = cur_user(); g = session.get('guest', False)
    if not u and not g: return redirect('/login')
    return render_template('catalog.html', tests=_cached_scan(), user=u, is_guest=g)

@app.route('/assignments')
@require_login
def assignments():
    u = cur_user()
    my_assignments = db.get_assignments(student_id=u['id'])
    tests = _cached_scan()
    from datetime import datetime as dt
    return render_template('assignments.html', assignments=my_assignments, tests=tests, user=u,
        now_date=dt.utcnow().strftime('%Y-%m-%d'))

@app.route('/api/tests')
def api_tests():
    return jsonify(_cached_scan())

@app.route('/api/module/<filename>')
def api_module(filename):
    fp = safe_path(TESTS_DIR, filename)
    if not fp or not os.path.exists(fp): abort(404)
    mi = request.args.get('module_index', 0, type=int)
    parsed = _cached_parse(fp)
    if mi >= len(parsed['modules']): abort(404)
    mod = parsed['modules'][mi]
    pages = pages_to_html(build_question_list(mod))
    is_practice = request.args.get('practice') == 'true'
    # Strip answers for client (keep in practice mode for instant feedback)
    client_pages = []
    for p in pages:
        cp = dict(p)
        if not is_practice:
            cp.pop('answer', None); cp.pop('cloze_answers', None); cp.pop('cloze_fills', None)
        cp.pop('explanation', None)  # Explanations only available via /api/explanations in review mode
        client_pages.append(cp)
    return jsonify({
        'header': parsed['header'],
        'module_info': {'section': mod['section'], 'module': mod['module'], 'timer_minutes': mod['timer_minutes']},
        'pages': client_pages,
    })

@app.route('/api/grade', methods=['POST'])
def api_grade():
    """Server-side grading. Returns scores and per-question details."""
    u = cur_user(); g = session.get('guest', False)
    if not u and not g: abort(401)  # Require login or guest session
    data = request.get_json()
    if not data: abort(400)
    fp = safe_path(TESTS_DIR, data.get('filename',''))
    if not fp or not os.path.exists(fp): abort(404)
    mi = data.get('module_index', 0)
    ans = data.get('answers', {})
    times = data.get('times', {})
    parsed = _cached_parse(fp)
    if mi >= len(parsed['modules']): abort(404)
    mod = parsed['modules'][mi]
    pages = build_question_list(mod)
    correct = 0; total = 0; details = []
    for pg in pages:
        qid = str(pg.get('question_id',''))
        qt = pg.get('question_type','')
        ua = ans.get(qid)
        ts = times.get(qid, 0)
        if qt == 'mc':
            total += 1; ok = ua == pg.get('answer','')
            if ok: correct += 1
            details.append({'qid':qid,'type':'mc','correct':ok,'user':ua or '—','expected':pg.get('answer',''),'time':ts})
        elif qt == 'cloze':
            fills = pg.get('cloze_fills',[]); words = pg.get('cloze_answers',[])
            ua_list = ua if isinstance(ua, list) else []
            for i, ef in enumerate(fills):
                total += 1; uv = (ua_list[i] if i < len(ua_list) else '').strip()
                ok = uv.lower() == ef.lower()
                if ok: correct += 1
                details.append({'qid':f'{qid}.{i+1}','type':'cloze','correct':ok,'user':uv or '—',
                    'expected':ef,'fullWord':words[i] if i<len(words) else '','time':ts if i==0 else 0})
        elif qt == 'build_sentence':
            total += 1
            exp = re.sub(r'[?!.]','',pg.get('answer','').strip().lower())
            usr = re.sub(r'[?!.]','',(ua or '').strip().lower())
            ok = usr == exp
            if ok: correct += 1
            details.append({'qid':qid,'type':'build_sentence','correct':ok,'user':ua or '—','expected':pg.get('answer',''),'time':ts})
        elif qt in ('email','discussion'):
            wc = len((ua or '').split()) if ua else 0
            details.append({'qid':qid,'type':qt,'user':ua,'wordCount':wc,'time':ts})
        elif qt in ('listen_repeat','interview'):
            details.append({'qid':qid,'type':qt,'hasRecording':ua=='[audio recorded]','time':ts})
    # Client response: no expected answers visible
    client_details = []
    for d in details:
        cd = dict(d)
        cd.pop('expected', None)
        cd.pop('fullWord', None)
        client_details.append(cd)
    return jsonify({
        'section':mod['section'],'moduleNum':mod['module'],
        'score':{'correct':correct,'total':total},
        'details': client_details,
    })

@app.route('/api/save-results', methods=['POST'])
def api_save_results():
    u = cur_user()
    if not u: return jsonify({'ok':False}), 401
    data = request.get_json()
    if not data: abort(400)
    # Enrich sections with expected answers (client doesn't have them)
    sections = data.get('sections', [])
    test_id = data.get('test_id', '')
    tests = _cached_scan()
    if test_id in tests:
        # Build answer key from all modules
        answer_key = {}  # {qid: {'expected': ..., 'fullWord': ...}}
        t = tests[test_id]
        seen_files = set()
        for mod_info in t['modules']:
            fn = mod_info['filename']
            if fn in seen_files: continue
            seen_files.add(fn)
            fp = safe_path(TESTS_DIR, fn)
            if fp and os.path.exists(fp):
                parsed = _cached_parse(fp)
                for mod in parsed['modules']:
                    for pg in build_question_list(mod):
                        qid = str(pg.get('question_id', ''))
                        qt = pg.get('question_type', '')
                        if qt == 'mc':
                            answer_key[qid] = {'expected': pg.get('answer', '')}
                        elif qt == 'cloze':
                            fills = pg.get('cloze_fills', [])
                            words = pg.get('cloze_answers', [])
                            for i, ef in enumerate(fills):
                                cqid = f'{qid}.{i+1}'
                                answer_key[cqid] = {'expected': ef, 'fullWord': words[i] if i < len(words) else ef}
                        elif qt == 'build_sentence':
                            answer_key[qid] = {'expected': pg.get('answer', '')}
        # Merge expected answers into section details
        for sec in sections:
            for d in sec.get('details', []):
                qid = str(d.get('qid', ''))
                if qid in answer_key:
                    d.update(answer_key[qid])
    result_id = db.save_result(u['id'], test_id, data.get('test_name',''),
        data.get('practice',False), data.get('total_correct',0),
        data.get('total_questions',0), json.dumps(sections))
    # Clean up the session if provided
    session_id = data.get('session_id')
    if session_id:
        try: db.finish_session(session_id)
        except Exception: pass
    return jsonify({'ok':True, 'result_id': result_id})

# ===== Server-side Test Sessions =====

@app.route('/api/session/start', methods=['POST'])
@require_login
def api_session_start():
    """Start or resume a test session. Returns session state."""
    u = cur_user()
    data = request.get_json()
    if not data: abort(400)
    test_id = data.get('test_id', '')
    mode = data.get('mode', 'full')
    section = data.get('section') or None
    practice = data.get('practice', False)
    playlist = data.get('playlist', [])
    # Check for existing active session
    existing = db.get_active_session(u['id'], test_id, mode, section, practice)
    if existing:
        return jsonify({
            'session_id': existing['id'],
            'resumed': True,
            'playlist_idx': existing['playlist_idx'],
            'answers': json.loads(existing['answers_json']) if existing['answers_json'] else {},
            'current_page': existing['current_page'],
            'timer_left': existing['timer_left'],
            'question_times': json.loads(existing['question_times_json']) if existing['question_times_json'] else {},
            'completed': json.loads(existing['completed_json']) if existing['completed_json'] else [],
        })
    # Create new session
    timer_left = 0
    if playlist:
        # Use first module's timer
        tests = _cached_scan()
        if test_id in tests:
            mods = tests[test_id]['modules']
            if mode == 'section':
                mods = [m for m in mods if m['section'] == section]
            if mods:
                timer_left = mods[0].get('timer_minutes', 0) * 60
    sid = db.create_session(u['id'], test_id, mode, section, practice, json.dumps(playlist), timer_left)
    return jsonify({
        'session_id': sid,
        'resumed': False,
        'playlist_idx': 0,
        'answers': {},
        'current_page': 0,
        'timer_left': timer_left,
        'question_times': {},
        'completed': [],
    })

@app.route('/api/session/<int:sid>/save', methods=['POST'])
@require_login
def api_session_save(sid):
    """Save current progress: answers, page, timer, question times."""
    u = cur_user()
    sess = db.get_session(sid)
    if not sess or sess['user_id'] != u['id']: abort(403)
    if sess['finished']: return jsonify({'ok': False, 'error': 'session finished'})
    data = request.get_json()
    if not data: abort(400)
    db.save_session_progress(
        sid,
        json.dumps(data.get('answers', {})),
        data.get('current_page', 0),
        data.get('timer_left', 0),
        json.dumps(data.get('question_times', {}))
    )
    return jsonify({'ok': True})

@app.route('/api/session/<int:sid>/advance', methods=['POST'])
@require_login
def api_session_advance(sid):
    """Advance to next module after grading. Stores graded result in completed list."""
    u = cur_user()
    sess = db.get_session(sid)
    if not sess or sess['user_id'] != u['id']: abort(403)
    data = request.get_json()
    if not data: abort(400)
    new_idx = data.get('playlist_idx', sess['playlist_idx'] + 1)
    playlist = json.loads(sess['playlist_json']) if sess['playlist_json'] else []
    # Validate bounds
    if new_idx < 0 or new_idx > len(playlist):
        return jsonify({'ok': False, 'error': 'playlist_idx out of bounds'}), 400
    # Merge the new graded result into the completed list
    completed = json.loads(sess['completed_json']) if sess['completed_json'] else []
    graded = data.get('graded_result')
    if graded:
        completed.append(graded)
    # Get timer for next module
    timer_left = 0
    if new_idx < len(playlist):
        next_mod = playlist[new_idx]
        timer_left = next_mod.get('timer_minutes', 0) * 60
    db.advance_session(sid, new_idx, json.dumps(completed), '{}', timer_left)
    return jsonify({'ok': True, 'timer_left': timer_left})

@app.route('/api/session/<int:sid>', methods=['GET'])
@require_login
def api_session_get(sid):
    """Load full session state."""
    u = cur_user()
    sess = db.get_session(sid)
    if not sess or sess['user_id'] != u['id']: abort(403)
    return jsonify({
        'session_id': sess['id'],
        'test_id': sess['test_id'],
        'mode': sess['mode'],
        'section': sess['section'],
        'practice': bool(sess['practice']),
        'playlist_idx': sess['playlist_idx'],
        'answers': json.loads(sess['answers_json']) if sess['answers_json'] else {},
        'current_page': sess['current_page'],
        'timer_left': sess['timer_left'],
        'question_times': json.loads(sess['question_times_json']) if sess['question_times_json'] else {},
        'completed': json.loads(sess['completed_json']) if sess['completed_json'] else [],
        'finished': bool(sess['finished']),
    })

@app.route('/api/session/<int:sid>', methods=['DELETE'])
@require_login
def api_session_delete(sid):
    """Delete/abandon a session."""
    u = cur_user()
    sess = db.get_session(sid)
    if not sess or sess['user_id'] != u['id']: abort(403)
    db.delete_session(sid)
    # Clean up session recordings
    sess_rec_dir = os.path.join(RECORDINGS_DIR, 'session_' + str(sid))
    if os.path.isdir(sess_rec_dir):
        shutil.rmtree(sess_rec_dir, ignore_errors=True)
    return jsonify({'ok': True})

@app.route('/api/session/<int:sid>/upload-recording', methods=['POST'])
@require_login
def api_session_upload_recording(sid):
    """Upload recordings during a test session (per-module)."""
    u = cur_user()
    sess = db.get_session(sid)
    if not sess or sess['user_id'] != u['id']: abort(403)
    dest = os.path.join(RECORDINGS_DIR, 'session_' + str(sid))
    os.makedirs(dest, exist_ok=True)
    saved = []
    for key, f in request.files.items():
        if not key.startswith('rec_'): continue
        qid = key[4:]
        safe_qid = re.sub(r'[^a-zA-Z0-9.\-]', '_', qid)
        ext = 'ogg'
        if f.content_type and 'webm' in f.content_type: ext = 'webm'
        elif f.content_type and 'mp4' in f.content_type: ext = 'mp4'
        filepath = os.path.join(dest, f'{safe_qid}.{ext}')
        f.save(filepath)
        saved.append(qid)
    return jsonify({'ok': True, 'saved': saved})

@app.route('/api/session/<int:sid>/finalize-recordings/<int:result_id>', methods=['POST'])
@require_login
def api_session_finalize_recordings(sid, result_id):
    """Move session recordings to permanent result storage."""
    u = cur_user()
    sess = db.get_session(sid)
    if not sess or sess['user_id'] != u['id']: abort(403)
    r = db.get_result_by_id(result_id)
    if not r or r['user_id'] != u['id']: abort(403)
    sess_dir = os.path.join(RECORDINGS_DIR, 'session_' + str(sid))
    result_dir = os.path.join(RECORDINGS_DIR, str(result_id))
    if os.path.isdir(sess_dir):
        os.makedirs(result_dir, exist_ok=True)
        for fname in os.listdir(sess_dir):
            src = os.path.join(sess_dir, fname)
            dst = os.path.join(result_dir, fname)
            shutil.move(src, dst)
        shutil.rmtree(sess_dir, ignore_errors=True)
    return jsonify({'ok': True})

@app.route('/test/<test_id>')
def take_test(test_id):
    u = cur_user(); g = session.get('guest', False)
    if not u and not g: return redirect('/login')
    tests = _cached_scan()
    if test_id not in tests: abort(404)
    return render_template('test.html', test_info=tests[test_id], user=u, is_guest=g)

@app.route('/audio/<path:filepath>')
def serve_audio(filepath):
    fp = safe_path(TESTS_DIR, filepath)
    if not fp or not os.path.exists(fp): abort(404)
    return send_from_directory(os.path.dirname(fp), os.path.basename(fp), mimetype='audio/ogg')

# ===== Recording upload and playback =====

RECORDINGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'recordings')

@app.route('/api/upload-recording/<int:result_id>', methods=['POST'])
@require_login
def api_upload_recording(result_id):
    """Upload audio recording files for a result."""
    u = cur_user()
    r = db.get_result_by_id(result_id)
    if not r: abort(404)
    if r['user_id'] != u['id']: abort(403)
    dest = os.path.join(RECORDINGS_DIR, str(result_id))
    os.makedirs(dest, exist_ok=True)
    saved = []
    for key, f in request.files.items():
        if not key.startswith('rec_'): continue
        qid = key[4:]  # strip 'rec_' prefix
        # Sanitize qid: only allow alphanumeric, dots, dashes
        safe_qid = re.sub(r'[^a-zA-Z0-9.\-]', '_', qid)
        ext = 'ogg'
        if f.content_type and 'webm' in f.content_type: ext = 'webm'
        elif f.content_type and 'mp4' in f.content_type: ext = 'mp4'
        filepath = os.path.join(dest, f'{safe_qid}.{ext}')
        f.save(filepath)
        saved.append(qid)
    return jsonify({'ok': True, 'saved': saved})

@app.route('/recordings/<int:result_id>/<qid>')
@require_login
def serve_recording(result_id, qid):
    """Serve a recording file. Teachers/admins and the student can access."""
    u = cur_user()
    r = db.get_result_by_id(result_id)
    if not r: abort(404)
    if u['role'] == 'student' and r['user_id'] != u['id']: abort(403)
    safe_qid = re.sub(r'[^a-zA-Z0-9.\-]', '_', qid)
    dest = os.path.join(RECORDINGS_DIR, str(result_id))
    if not os.path.isdir(dest): abort(404)
    # Find the file regardless of extension
    for ext in ('ogg', 'webm', 'mp4'):
        fp = os.path.join(dest, f'{safe_qid}.{ext}')
        if os.path.exists(fp):
            mime = {'ogg': 'audio/ogg', 'webm': 'audio/webm', 'mp4': 'audio/mp4'}.get(ext, 'audio/ogg')
            return send_from_directory(dest, f'{safe_qid}.{ext}', mimetype=mime)
    abort(404)

@app.route('/api/recordings/<int:result_id>')
@require_login
def api_list_recordings(result_id):
    """List available recording qids for a result."""
    u = cur_user()
    r = db.get_result_by_id(result_id)
    if not r: abort(404)
    if u['role'] == 'student' and r['user_id'] != u['id']: abort(403)
    dest = os.path.join(RECORDINGS_DIR, str(result_id))
    if not os.path.isdir(dest): return jsonify([])
    qids = []
    for fname in os.listdir(dest):
        name, _ = os.path.splitext(fname)
        qids.append(name)
    return jsonify(qids)

# ===== Admin =====

@app.route('/admin/users')
@require_login
@require_role('admin')
def admin_users():
    return render_template('admin_users.html', users=db.list_users(), user=cur_user())

@app.route('/admin/users/create', methods=['POST'])
@require_login
@require_role('admin')
def admin_create_user():
    un = request.form.get('username','').strip()
    pw = request.form.get('password','')
    if not un or not pw: flash('Username and password required'); return redirect('/admin/users')
    if len(pw) < 6: flash('Password must be at least 6 characters'); return redirect('/admin/users')
    try:
        db.create_user(un, pw, request.form.get('role','student'), request.form.get('display_name','').strip())
        flash(f'User "{un}" created')
    except Exception as e: flash(f'Error: {e}')
    return redirect('/admin/users')

@app.route('/admin/users/<int:uid>/delete', methods=['POST'])
@require_login
@require_role('admin')
def admin_delete_user(uid):
    if uid == session.get('user_id'): flash('Cannot delete yourself')
    else: db.delete_user(uid); flash('User deleted')
    return redirect('/admin/users')

@app.route('/admin/users/<int:uid>/edit', methods=['POST'])
@require_login
@require_role('admin')
def admin_edit_user(uid):
    pw = request.form.get('password','').strip()
    new_role = request.form.get('role')
    # Prevent admin from changing their own role (could lock out the last admin)
    if uid == session.get('user_id') and new_role and new_role != 'admin':
        flash('Cannot change your own role'); return redirect('/admin/users')
    if pw and len(pw) < 6:
        flash('Password must be at least 6 characters'); return redirect('/admin/users')
    db.update_user(uid, display_name=request.form.get('display_name'),
        password=pw or None, role=new_role)
    flash('User updated'); return redirect('/admin/users')

@app.route('/admin/users/import', methods=['POST'])
@require_login
@require_role('admin')
def admin_import_users():
    import csv, io
    f = request.files.get('csv_file')
    if not f: flash('No file uploaded'); return redirect('/admin/users')
    try:
        text = f.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(text))
        users = []
        pw_errors = []
        for row in reader:
            un = (row.get('username') or '').strip()
            pw = (row.get('password') or '').strip()
            if un and pw:
                if len(pw) < 6:
                    pw_errors.append(f'{un}: password too short (min 6)')
                    continue
                users.append({
                    'username': un, 'password': pw,
                    'role': (row.get('role') or 'student').strip(),
                    'display_name': (row.get('display_name') or un).strip(),
                })
        if not users and not pw_errors: flash('No valid rows found in CSV'); return redirect('/admin/users')
        if users:
            created, db_errors = db.bulk_create_users(users)
        else:
            created, db_errors = 0, []
        all_errors = pw_errors + db_errors
        msg = f'{created} user(s) created'
        if all_errors: msg += f', {len(all_errors)} error(s): ' + '; '.join(all_errors[:3])
        flash(msg)
    except Exception as e:
        flash(f'Import error: {e}')
    return redirect('/admin/users')

@app.route('/admin/announcement', methods=['POST'])
@require_login
@require_role('admin')
def admin_announcement():
    content = request.form.get('content', '').strip()
    if content:
        db.create_announcement(cur_user()['id'], content)
        flash('Announcement posted')
    return redirect('/admin/users')

@app.route('/admin/announcement/dismiss', methods=['POST'])
@require_login
@require_role('admin')
def admin_dismiss_announcement():
    db.dismiss_announcement()
    flash('Announcement dismissed')
    return redirect('/admin/users')

# ===== Teacher =====

@app.route('/teacher/results')
@require_login
@require_role('admin','teacher')
def teacher_results():
    page = request.args.get('page', 1, type=int)
    per_page = 50
    total = db.count_results()
    results = db.get_results(limit=per_page, offset=(page-1)*per_page)
    total_pages = max(1, (total + per_page - 1) // per_page)
    return render_template('teacher_results.html', results=results,
        students=db.list_users(role='student'), tests=_cached_scan(), user=cur_user(),
        page=page, total_pages=total_pages)

@app.route('/teacher/assign', methods=['POST'])
@require_login
@require_role('admin','teacher')
def teacher_assign():
    sid = request.form.get('student_id', type=int)
    tid = request.form.get('test_id','')
    sec = request.form.get('section','').strip() or None
    due = request.form.get('due_date','').strip() or None
    if sid and tid: db.assign_test(cur_user()['id'], sid, tid, sec, due); flash('Test assigned')
    return redirect('/teacher/results')

@app.route('/teacher/assign/<int:aid>/delete', methods=['POST'])
@require_login
@require_role('admin','teacher')
def teacher_remove_assignment(aid):
    """Remove a test assignment (#4)."""
    db.remove_assignment(aid)
    flash('Assignment removed')
    return redirect('/teacher/progress')

@app.route('/teacher/progress')
@require_login
@require_role('admin','teacher')
def teacher_progress():
    students = db.list_users(role='student')
    progress_data = []
    for s in students:
        assignments = db.get_assignments(student_id=s['id'])
        results = db.get_results(user_id=s['id'], limit=10000)  # Need all for progress check
        # Build set of completed (test_id, section|None) tuples (#5)
        completed_keys = set()
        for r in results:
            if not r['practice']:
                try:
                    secs = json.loads(r['sections_json']) if r['sections_json'] else []
                except Exception: secs = []
                completed_sections = {sec.get('section') for sec in secs}
                completed_keys.add((r['test_id'], None))  # full test
                for cs in completed_sections:
                    completed_keys.add((r['test_id'], cs))
        total = len(assignments)
        done = sum(1 for a in assignments if (a['test_id'], a.get('section')) in completed_keys)
        progress_data.append({
            'student': s,
            'total': total,
            'done': done,
            'pct': round(done / total * 100) if total > 0 else 0,
            'assignments': assignments,
        })
    return render_template('teacher_progress.html', progress=progress_data, user=cur_user())

# ===== Account =====

@app.route('/account', methods=['GET','POST'])
@require_login
def account():
    u = cur_user()
    if request.method == 'POST':
        old_pw = request.form.get('old_password','')
        new_pw = request.form.get('new_password','')
        confirm_pw = request.form.get('confirm_password','')
        if not db.authenticate(u['username'], old_pw):
            flash('Current password is incorrect')
        elif new_pw != confirm_pw:
            flash('New passwords do not match')
        elif len(new_pw) < 6:
            flash('New password must be at least 6 characters')
        else:
            db.update_user(u['id'], password=new_pw)
            flash('Password changed successfully')
        return redirect('/account')
    return render_template('account.html', user=u)

# ===== Results Detail =====

@app.route('/results/<int:result_id>')
@require_login
def result_detail(result_id):
    u = cur_user()
    r = db.get_result_by_id(result_id)
    if not r: abort(404)
    # Students can only view their own; teachers/admins can view all
    if u['role'] == 'student' and r['user_id'] != u['id']: abort(403)
    return render_template('result_detail.html', result=r, user=u)

# ===== Review Mode =====

@app.route('/review/<int:result_id>')
@require_login
def review_test(result_id):
    u = cur_user()
    r = db.get_result_by_id(result_id)
    if not r: abort(404)
    if u['role'] == 'student' and r['user_id'] != u['id']: abort(403)
    tests = _cached_scan()
    test_info = tests.get(r['test_id'])
    if not test_info: abort(404)
    return render_template('review.html', result=r, test_info=test_info, user=u)

@app.route('/api/review-data/<int:result_id>')
@require_login
def api_review_data(result_id):
    u = cur_user()
    r = db.get_result_by_id(result_id)
    if not r: abort(404)
    if u['role'] == 'student' and r['user_id'] != u['id']: abort(403)
    tests = _cached_scan()
    test_info = tests.get(r['test_id'])
    if not test_info: return jsonify({'error': 'test not found'}), 404
    # Parse graded results
    try:
        sections = json.loads(r['sections_json']) if r['sections_json'] else []
    except Exception: sections = []
    detail_map = {}
    for sec in sections:
        for d in sec.get('details', []):
            detail_map[str(d.get('qid', ''))] = d
    # Load full test modules with HTML
    modules = []
    for mod_info in test_info['modules']:
        fp = safe_path(TESTS_DIR, mod_info['filename'])
        if not fp or not os.path.exists(fp): continue
        parsed = _cached_parse(fp)
        mi = mod_info['module_index']
        if mi >= len(parsed['modules']): continue
        mod = parsed['modules'][mi]
        pages = pages_to_html(build_question_list(mod))
        for p in pages:
            qid = str(p.get('question_id', ''))
            p['graded'] = detail_map.get(qid, {})
            if p.get('question_type') == 'cloze':
                p['cloze_details'] = [detail_map.get(f'{qid}.{i+1}', {}) for i in range(len(p.get('cloze_fills', [])))]
        modules.append({'section': mod['section'], 'module': mod['module'], 'pages': pages})
    # Load notes, comments, explanations, recordings
    notes = db.get_notes(u['id'], result_id)
    comments = db.get_teacher_comments(result_id)
    db_expl = db.get_explanations(r['test_id'])
    md_expl = {}
    seen = set()
    for mi in test_info['modules']:
        fn = mi['filename']
        if fn in seen: continue
        seen.add(fn)
        fp = safe_path(TESTS_DIR, fn)
        if fp and os.path.exists(fp):
            parsed = _cached_parse(fp)
            for mod in parsed['modules']:
                for pg in build_question_list(mod):
                    qid = str(pg.get('question_id', ''))
                    if pg.get('explanation'):
                        md_expl[qid] = md_html(pg['explanation'])
    explanations = {**md_expl, **db_expl}
    rec_dir = os.path.join(RECORDINGS_DIR, str(result_id))
    recs = [os.path.splitext(f)[0] for f in os.listdir(rec_dir)] if os.path.isdir(rec_dir) else []
    return jsonify({'modules': modules, 'notes': notes, 'comments': comments,
        'explanations': explanations, 'recordings': recs, 'result_id': result_id})

# ===== History =====

@app.route('/history')
@require_login
def history_page():
    u = cur_user()
    page = request.args.get('page', 1, type=int)
    per_page = 50
    total = db.count_results(user_id=u['id'])
    results = db.get_results(user_id=u['id'], limit=per_page, offset=(page-1)*per_page)
    total_pages = max(1, (total + per_page - 1) // per_page)
    return render_template('history.html', results=results, user=u,
        page=page, total_pages=total_pages)

# ===== Notes API =====

@app.route('/api/notes/<int:result_id>', methods=['GET'])
@require_login
def api_get_notes(result_id):
    u = cur_user()
    r = db.get_result_by_id(result_id)
    if not r: abort(404)
    if u['role'] == 'student' and r['user_id'] != u['id']: abort(403)
    return jsonify(db.get_notes(u['id'], result_id))

@app.route('/api/notes/<int:result_id>', methods=['POST'])
@require_login
def api_save_note(result_id):
    u = cur_user()
    r = db.get_result_by_id(result_id)
    if not r: abort(404)
    if u['role'] == 'student' and r['user_id'] != u['id']: abort(403)
    data = request.get_json()
    if not data: abort(400)
    db.save_note(u['id'], result_id, data.get('question_id', ''), data.get('note', ''))
    return jsonify({'ok': True})

# ===== Dashboard Analytics =====

@app.route('/dashboard')
@require_login
def dashboard():
    u = cur_user()
    if u['role'] == 'student':
        return render_template('dashboard.html', user=u, students=None)
    # Teachers/admins see student list
    students = db.list_users(role='student')
    return render_template('dashboard.html', user=u, students=students)

@app.route('/api/analytics/<int:uid>')
@require_login
def api_analytics(uid):
    u = cur_user()
    # Students can only view their own; teachers/admins can view any
    if u['role'] == 'student' and u['id'] != uid: abort(403)
    results = db.get_analytics(uid)
    # Build analytics data
    score_history = []
    section_totals = {}
    section_correct = {}
    for r in results:
        if r['total_questions'] > 0:
            pct = round(r['total_correct'] / r['total_questions'] * 100)
            score_history.append({'date': r['date'][:10], 'pct': pct, 'name': r['test_name'] or r['test_id']})
        try:
            sections = json.loads(r['sections_json']) if r['sections_json'] else []
        except Exception: sections = []
        for sec in sections:
            s = sec.get('section', 'unknown')
            sc = sec.get('score', {})
            section_totals[s] = section_totals.get(s, 0) + sc.get('total', 0)
            section_correct[s] = section_correct.get(s, 0) + sc.get('correct', 0)
    section_breakdown = []
    for s in ['reading', 'listening', 'writing', 'speaking']:
        t = section_totals.get(s, 0)
        c = section_correct.get(s, 0)
        section_breakdown.append({'section': s, 'correct': c, 'total': t, 'pct': round(c/t*100) if t > 0 else 0})
    return jsonify({'score_history': score_history, 'section_breakdown': section_breakdown})

# ===== Teacher Comments =====

@app.route('/api/comments/<int:result_id>', methods=['GET'])
@require_login
def api_get_comments(result_id):
    u = cur_user()
    r = db.get_result_by_id(result_id)
    if not r: abort(404)
    if u['role'] == 'student' and r['user_id'] != u['id']: abort(403)
    return jsonify(db.get_teacher_comments(result_id))

@app.route('/api/comments/<int:result_id>', methods=['POST'])
@require_login
@require_role('admin', 'teacher')
def api_save_comment(result_id):
    u = cur_user()
    data = request.get_json()
    if not data: abort(400)
    db.save_teacher_comment(u['id'], result_id, data.get('question_id'), data.get('comment', ''))
    return jsonify({'ok': True})

# ===== Question Explanations =====

@app.route('/api/explanations/<test_id>', methods=['GET'])
@require_login
def api_get_explanations(test_id):
    # Merge: markdown-based explanations (from parsed test) + DB explanations
    db_expl = db.get_explanations(test_id)
    # Get markdown explanations from ALL test files (#12)
    tests = _cached_scan()
    md_expl = {}
    if test_id in tests:
        t = tests[test_id]
        seen_files = set()
        for mod_info in t['modules']:
            fn = mod_info['filename']
            if fn in seen_files: continue
            seen_files.add(fn)
            fp = safe_path(TESTS_DIR, fn)
            if fp and os.path.exists(fp):
                parsed = _cached_parse(fp)
                for mod in parsed['modules']:
                    pages = build_question_list(mod)
                    for pg in pages:
                        qid = str(pg.get('question_id', ''))
                        if pg.get('explanation'):
                            md_expl[qid] = md_html(pg['explanation'])
    # DB explanations override markdown
    merged = {**md_expl}
    for qid, expl in db_expl.items():
        merged[qid] = expl
    return jsonify(merged)

@app.route('/api/explanations/<test_id>', methods=['POST'])
@require_login
@require_role('admin', 'teacher')
def api_save_explanation(test_id):
    u = cur_user()
    data = request.get_json()
    if not data: abort(400)
    db.save_explanation(test_id, data.get('question_id', ''), data.get('explanation', ''), u['id'])
    return jsonify({'ok': True})

# ===== Batch Export =====

@app.route('/teacher/export', methods=['POST'])
@require_login
@require_role('admin', 'teacher')
def teacher_batch_export():
    import csv as csv_mod, io
    student_ids = request.form.getlist('student_ids', type=int)
    fmt = request.form.get('format', 'csv')
    if not student_ids: flash('No students selected'); return redirect('/teacher/results')
    all_results = []
    for sid in student_ids:
        results = db.get_results(user_id=sid, limit=10000)  # Export all, no pagination
        all_results.extend(results)
    if fmt == 'csv':
        buf = io.StringIO()
        writer = csv_mod.writer(buf)
        writer.writerow(['Student', 'Username', 'Test', 'Score', 'Total', 'Percent', 'Practice', 'Date'])
        for r in all_results:
            pct = round(r['total_correct']/r['total_questions']*100) if r['total_questions']>0 else ''
            writer.writerow([r.get('display_name',''), r.get('username',''), r.get('test_name',''),
                r['total_correct'], r['total_questions'], pct,
                'Yes' if r['practice'] else 'No', filter_fmtdate(r['date'])])
        output = io.BytesIO(buf.getvalue().encode('utf-8-sig'))
        return send_file(output, mimetype='text/csv', download_name='results_export.csv', as_attachment=True)
    flash('Unsupported format'); return redirect('/teacher/results')

# ===== PDF =====

@app.route('/api/export-pdf', methods=['POST'])
@require_login
def export_pdf():
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    try: pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light')); cjk = 'STSong-Light'
    except Exception: cjk = 'Helvetica'
    data = request.get_json()
    if not data: abort(400)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=25*mm, rightMargin=25*mm, topMargin=20*mm, bottomMargin=20*mm)
    ts = ParagraphStyle(name='T2', fontName='Helvetica-Bold', fontSize=18, leading=22, spaceAfter=4)
    ss = ParagraphStyle(name='Sub', fontName='Helvetica', fontSize=10, textColor=HexColor('#8e8e93'), spaceAfter=12)
    sec_s = ParagraphStyle(name='Sec', fontName='Helvetica-Bold', fontSize=13, leading=16, spaceBefore=16, spaceAfter=8)
    bf = cjk if data.get('lang')=='zh' else 'Helvetica'
    story = []
    story.append(Paragraph(data.get('test_name','TOEFL Practice Test'), ts))
    parts = [(data.get('date','') or '')[:10]]
    if data.get('student_name'): parts.append(data['student_name'])
    if data.get('student_id'): parts.append('ID: '+data['student_id'])
    if data.get('practice'): parts.append('<font color="#ff9500"><b>PRACTICE</b></font>')
    story.append(Paragraph(' &bull; '.join(parts), ss))
    story.append(HRFlowable(width='100%', thickness=0.5, color=HexColor('#c6c6c8')))
    story.append(Spacer(1, 6*mm))
    gn, rd, mu = HexColor('#34c759'), HexColor('#ff3b30'), HexColor('#8e8e93')
    for r in data.get('results',[]):
        sc = r.get('score',{}); c_ = sc.get('correct',0); t_ = sc.get('total',0)
        stxt = f"{r.get('section','').capitalize()} — Module {r.get('moduleNum',1)}"
        if t_>0: stxt += f"  ({c_}/{t_}, {round(c_/t_*100)}%)"
        story.append(Paragraph(stxt, sec_s))
        rows = [['Q','','Your Answer','Correct','Time']]
        for d in r.get('details',[]):
            q=str(d.get('qid','')); dt=d.get('type',''); tm=str(d.get('time',0))+'s' if d.get('time') else ''
            if dt in ('mc','cloze','build_sentence'):
                rows.append([q,'\u2713' if d.get('correct') else '\u2717',str(d.get('user',''))[:40],str(d.get('fullWord') or d.get('expected',''))[:40],tm])
            elif dt in ('email','discussion'):
                rows.append([q,'\u270E',f"{dt} ({d.get('wordCount',0)} words)",'',tm])
            elif dt in ('listen_repeat','interview'):
                rows.append([q,'\U0001F3A4' if d.get('hasRecording') else '\u2014','Recorded' if d.get('hasRecording') else 'No recording','',tm])
        if len(rows)>1:
            tbl = Table(rows, colWidths=[30,18,170,170,40], repeatRows=1)
            sty = [('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),9),('FONTNAME',(0,1),(-1,-1),bf),
                ('TEXTCOLOR',(0,0),(-1,0),mu),('LINEBELOW',(0,0),(-1,0),0.5,HexColor('#c6c6c8')),
                ('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3),('VALIGN',(0,0),(-1,-1),'TOP')]
            for i,d in enumerate(r.get('details',[]),1):
                if d.get('correct') is True: sty.append(('TEXTCOLOR',(1,i),(1,i),gn))
                elif d.get('correct') is False: sty.append(('TEXTCOLOR',(1,i),(1,i),rd))
            tbl.setStyle(TableStyle(sty)); story.append(tbl)
        story.append(Spacer(1,4*mm))
    doc.build(story); buf.seek(0)
    return send_file(buf, mimetype='application/pdf', download_name=data.get('test_id','results')+'_results.pdf')

# ===== Init =====
with app.app_context():
    db.init_db(SITE_CONFIG)

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('-p','--port',type=int,default=8080)
    p.add_argument('--host',default='0.0.0.0')
    a = p.parse_args()
    print(f'Starting on http://{a.host}:{a.port}')
    print(f'Default admin: admin / admin')
    app.run(debug=True, host=a.host, port=a.port)
