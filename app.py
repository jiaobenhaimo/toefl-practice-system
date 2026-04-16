"""
app.py — Flask application for TOEFL Practice Test System.
Routes, middleware, and init. Shared helpers live in helpers.py.
"""
import os, json, secrets, re, shutil, io, csv
from datetime import datetime as dt
from flask import (
    Flask, render_template, jsonify, request, session,
    send_from_directory, abort, redirect, url_for, flash, send_file, g
)
from parser import build_question_list
import database as db
from helpers import (
    SITE_CONFIG, TESTS_DIR, RECORDINGS_DIR,
    cached_parse, cached_scan, md_html, pages_to_html, safe_path,
    cur_user, parse_json, get_result_or_403,
    require_login, require_role,
    fmtdate, fmtdate_full,
    check_rate_limit, record_attempt,
    lookup_band, section_band,
    _RL_BAND_TABLE, _WRITING_BAND_TABLE, _SPEAKING_BAND_TABLE,
)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['PERMANENT_SESSION_LIFETIME'] = 60 * 60 * 24 * 31
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB upload limit

@app.teardown_appcontext
def _close_db(exc):
    db.close_conn()

@app.context_processor
def inject_config():
    if not hasattr(g, '_announcement'):
        g._announcement = db.get_active_announcement()
    return {'site': SITE_CONFIG.get('site', {}), 'config': SITE_CONFIG, 'announcement': g._announcement}

app.template_filter('fmtdate')(fmtdate)
app.template_filter('fmtdate_full')(fmtdate_full)

# ===== CSRF Protection =====
def _csrf_token():
    if '_csrf' not in session:
        session['_csrf'] = secrets.token_hex(16)
    return session['_csrf']

@app.context_processor
def inject_csrf():
    return {'csrf_token': _csrf_token}

@app.before_request
def csrf_check():
    if request.method == 'POST' and request.content_type != 'application/json':
        token = request.form.get('_csrf', '')
        if not token or token != session.get('_csrf'):
            abort(403)

# ===== Auth routes =====

@app.route('/login', methods=['GET','POST'])
def login():
    if session.get('user_id'): return redirect('/')
    error = None
    if request.method == 'POST':
        ip = request.remote_addr or '0.0.0.0'
        if check_rate_limit(ip):
            error = 'Too many login attempts. Please wait a few minutes.'
            return render_template('login.html', error=error)
        record_attempt(ip)
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
    u = cur_user(); is_guest = session.get('guest', False)
    if not u and not is_guest: return redirect('/login')
    if u and u['role'] == 'student': return redirect('/assignments')
    if u and u['role'] == 'parent': return redirect('/parent')
    return redirect('/catalog')

@app.route('/catalog')
def catalog():
    u = cur_user(); is_guest = session.get('guest', False)
    if not u and not is_guest: return redirect('/login')
    return render_template('catalog.html', tests=cached_scan(), user=u, is_guest=is_guest)

@app.route('/assignments')
@require_login
def assignments():
    u = cur_user()
    my_assignments = db.get_assignments(student_id=u['id'])
    tests = cached_scan()
    completed_keys = db.get_completed_test_keys(u['id'])
    pending = [a for a in my_assignments if (a['test_id'], a.get('section')) not in completed_keys]
    return render_template('assignments.html', assignments=pending, tests=tests, user=u,
        now_date=dt.utcnow().strftime('%Y-%m-%d'))

@app.route('/api/module/<filename>')
def api_module(filename):
    fp = safe_path(TESTS_DIR, filename)
    if not fp or not os.path.exists(fp): abort(404)
    mi = request.args.get('module_index', 0, type=int)
    parsed = cached_parse(fp)
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
    u = cur_user(); is_guest = session.get('guest', False)
    if not u and not is_guest: abort(401)  # Require login or guest session
    data = request.get_json()
    if not data: abort(400)
    fp = safe_path(TESTS_DIR, data.get('filename',''))
    if not fp or not os.path.exists(fp): abort(404)
    mi = data.get('module_index', 0)
    ans = data.get('answers', {})
    times = data.get('times', {})
    parsed = cached_parse(fp)
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
    tests = cached_scan()
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
                parsed = cached_parse(fp)
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
    # Auto-populate error bank from wrong auto-graded answers (non-practice only)
    if not data.get('practice', False):
        _populate_error_bank(u['id'], test_id, sections, tests if test_id in tests else {})
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
            'answers': parse_json(existing['answers_json'], {}),
            'current_page': existing['current_page'],
            'timer_left': existing['timer_left'],
            'question_times': parse_json(existing['question_times_json'], {}),
            'completed': parse_json(existing['completed_json']),
        })
    # Create new session
    timer_left = 0
    if playlist:
        # Use first module's timer
        tests = cached_scan()
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
    playlist = parse_json(sess['playlist_json'])
    # Validate bounds
    if new_idx < 0 or new_idx > len(playlist):
        return jsonify({'ok': False, 'error': 'playlist_idx out of bounds'}), 400
    # Merge the new graded result into the completed list
    completed = parse_json(sess['completed_json'])
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
        'answers': parse_json(sess['answers_json'], {}),
        'current_page': sess['current_page'],
        'timer_left': sess['timer_left'],
        'question_times': parse_json(sess['question_times_json'], {}),
        'completed': parse_json(sess['completed_json']),
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
    u = cur_user(); is_guest = session.get('guest', False)
    if not u and not is_guest: return redirect('/login')
    tests = cached_scan()
    if test_id not in tests: abort(404)
    return render_template('test.html', test_info=tests[test_id], user=u, is_guest=is_guest)

@app.route('/audio/<path:filepath>')
def serve_audio(filepath):
    fp = safe_path(TESTS_DIR, filepath)
    if not fp or not os.path.exists(fp): abort(404)
    return send_from_directory(os.path.dirname(fp), os.path.basename(fp), mimetype='audio/ogg')

# ===== Recording upload and playback =====

@app.route('/recordings/<int:result_id>/<qid>')
@require_login
def serve_recording(result_id, qid):
    """Serve a recording file. Teachers/admins and the student can access."""
    r = get_result_or_403(result_id)
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

# ===== Admin =====

@app.route('/admin/users')
@require_login
@require_role('admin')
def admin_users():
    return render_template('admin_users.html', users=db.list_users(), user=cur_user(),
        students=db.list_users(role='student'))

@app.route('/admin/users/create', methods=['POST'])
@require_login
@require_role('admin')
def admin_create_user():
    un = request.form.get('username','').strip()
    pw = '12345678'  # Fixed default password
    if not un: flash('Username is required'); return redirect('/admin/users')
    role = request.form.get('role', 'student')
    try:
        uid = db.create_user(un, pw, role, request.form.get('display_name','').strip())
        # Link parent to student if applicable
        if role == 'parent':
            linked_id = request.form.get('linked_student_id', type=int)
            if linked_id:
                db.update_user(uid, linked_student_id=linked_id)
        flash(f'User "{un}" created (password: 12345678)')
    except Exception as e: flash(f'Error: {e}')
    return redirect('/admin/users')

@app.route('/admin/users/<int:uid>/delete', methods=['POST'])
@require_login
@require_role('admin')
def admin_delete_user(uid):
    if uid == session.get('user_id'):
        flash('Cannot delete yourself')
    else:
        # Clean up recording files for all the user's results
        results = db.get_results(user_id=uid, limit=100000)
        for r in results:
            rec_dir = os.path.join(RECORDINGS_DIR, str(r['id']))
            if os.path.isdir(rec_dir):
                shutil.rmtree(rec_dir, ignore_errors=True)
        # Clean up any session recordings
        conn = db.get_db()
        sess_rows = conn.execute("SELECT id FROM test_sessions WHERE user_id=?", (uid,)).fetchall()
        for sr in sess_rows:
            sess_dir = os.path.join(RECORDINGS_DIR, 'session_' + str(sr['id']))
            if os.path.isdir(sess_dir):
                shutil.rmtree(sess_dir, ignore_errors=True)
        db.delete_user(uid)
        flash('User deleted')
    return redirect('/admin/users')

@app.route('/admin/users/<int:uid>/edit', methods=['POST'])
@require_login
@require_role('admin')
def admin_edit_user(uid):
    new_username = request.form.get('username','').strip()
    new_display = request.form.get('display_name','').strip()
    new_role = request.form.get('role')
    reset_pw = request.form.get('reset_password') == 'on'
    # Prevent admin from changing their own role
    if uid == session.get('user_id') and new_role and new_role != 'admin':
        flash('Cannot change your own role'); return redirect('/admin/users')
    # Only allow student <-> teacher, not admin
    if new_role == 'admin':
        flash('Cannot assign admin role'); return redirect('/admin/users')
    db.update_user(uid, display_name=new_display or None,
        username=new_username or None,
        password='12345678' if reset_pw else None,
        role=new_role)
    msg = 'User updated'
    if reset_pw: msg += ' (password reset to 12345678)'
    flash(msg); return redirect('/admin/users')

@app.route('/admin/users/import', methods=['POST'])
@require_login
@require_role('admin')
def admin_import_users():
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
        students=db.list_users(role='student'), user=cur_user(),
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
    return redirect('/teacher/progress')

@app.route('/teacher/assign/<int:aid>/delete', methods=['POST'])
@require_login
@require_role('admin','teacher')
def teacher_remove_assignment(aid):
    """Remove a test assignment."""
    db.remove_assignment(aid)
    flash('Assignment removed')
    # Redirect back to the page that initiated the removal
    referrer = request.referrer
    if referrer and referrer.startswith(request.host_url):
        return redirect(referrer)
    return redirect('/teacher/progress')

@app.route('/teacher/progress')
@require_login
@require_role('admin','teacher')
def teacher_progress():
    students = db.list_users(role='student')
    student_ids = [s['id'] for s in students]
    all_data = db.get_all_progress_data(student_ids)
    progress_data = []
    for s in students:
        d = all_data.get(s['id'], {'assignments': [], 'completed_keys': set()})
        assignments = d['assignments']
        completed_keys = d['completed_keys']
        total = len(assignments)
        done = sum(1 for a in assignments if (a['test_id'], a.get('section')) in completed_keys)
        progress_data.append({
            'student': s,
            'total': total,
            'done': done,
            'pct': round(done / total * 100) if total > 0 else 0,
            'assignments': assignments,
        })
    return render_template('teacher_progress.html', progress=progress_data, user=cur_user(),
        tests=cached_scan())

# ===== Batch Export =====

@app.route('/teacher/export', methods=['POST'])
@require_login
@require_role('admin', 'teacher')
def teacher_batch_export():
    student_ids = request.form.getlist('student_ids', type=int)
    if not student_ids:
        flash('No students selected')
        return redirect('/teacher/results')
    all_results = []
    for sid in student_ids:
        all_results.extend(db.get_results(user_id=sid, limit=10000))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['Student', 'Username', 'Test', 'Score', 'Total', 'Percent', 'Practice', 'Date'])
    for r in all_results:
        pct = round(r['total_correct'] / r['total_questions'] * 100) if r['total_questions'] > 0 else ''
        writer.writerow([
            r.get('display_name', ''), r.get('username', ''), r.get('test_name', ''),
            r['total_correct'], r['total_questions'], pct,
            'Yes' if r['practice'] else 'No', fmtdate(r['date'])
        ])
    output = io.BytesIO(buf.getvalue().encode('utf-8-sig'))
    return send_file(output, mimetype='text/csv', download_name='results_export.csv', as_attachment=True)

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

# ===== Results Detail (redirect to review) =====

@app.route('/results/<int:result_id>')
@require_login
def result_detail(result_id):
    get_result_or_403(result_id)
    return redirect(f'/review/{result_id}')

# ===== Review Mode =====

@app.route('/review/<int:result_id>')
@require_login
def review_test(result_id):
    u = cur_user()
    r = get_result_or_403(result_id)
    tests = cached_scan()
    test_info = tests.get(r['test_id'])
    if not test_info:
        flash('The test file for this result is no longer available.')
        return redirect('/history')
    assignments = db.get_assignments(student_id=r['user_id'])
    is_assigning_teacher = any(a['teacher_id'] == u['id'] and a['test_id'] == r['test_id'] for a in assignments)
    return render_template('review.html', result=r, test_info=test_info, user=u,
        is_assigning_teacher=is_assigning_teacher)

@app.route('/api/review-data/<int:result_id>')
@require_login
def api_review_data(result_id):
    u = cur_user()
    r = get_result_or_403(result_id)
    tests = cached_scan()
    test_info = tests.get(r['test_id'])
    if not test_info: return jsonify({'error': 'test not found'}), 404
    sections = parse_json(r['sections_json'])
    # Build a set of (section, moduleNum) that were actually taken
    taken_modules = set()
    detail_map = {}
    for sec in sections:
        taken_modules.add((sec.get('section', ''), sec.get('moduleNum', 1)))
        for d in sec.get('details', []):
            detail_map[str(d.get('qid', ''))] = d
    # Load ONLY modules that were actually taken (not the entire test)
    modules = []
    for mod_info in test_info['modules']:
        fp = safe_path(TESTS_DIR, mod_info['filename'])
        if not fp or not os.path.exists(fp): continue
        parsed = cached_parse(fp)
        mi = mod_info['module_index']
        if mi >= len(parsed['modules']): continue
        mod = parsed['modules'][mi]
        # Skip modules that weren't in the graded results
        if (mod['section'], mod['module']) not in taken_modules:
            continue
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
            parsed = cached_parse(fp)
            for mod in parsed['modules']:
                for pg in build_question_list(mod):
                    qid = str(pg.get('question_id', ''))
                    if pg.get('explanation'):
                        md_expl[qid] = md_html(pg['explanation'])
    explanations = {**md_expl, **db_expl}
    rec_dir = os.path.join(RECORDINGS_DIR, str(result_id))
    recs = [os.path.splitext(f)[0] for f in os.listdir(rec_dir)] if os.path.isdir(rec_dir) else []
    # Extract rubric scores from comments
    rubric_scores = {}
    for key, val in comments.items():
        if key.startswith('_rubric_'):
            qid = key[8:]
            try: rubric_scores[qid] = int(val['comment'])
            except Exception: pass
    # Build section summaries for the overview
    section_summaries = []
    for sec in sections:
        sc = sec.get('score', {})
        section_summaries.append({
            'section': sec.get('section', ''),
            'moduleNum': sec.get('moduleNum', 1),
            'correct': sc.get('correct', 0),
            'total': sc.get('total', 0),
        })
    return jsonify({'modules': modules, 'notes': notes, 'comments': comments,
        'explanations': explanations, 'recordings': recs, 'result_id': result_id,
        'section_summaries': section_summaries,
        'student_name': r.get('display_name') or r.get('username') or '',
        'assigning_teacher_ids': [a['teacher_id'] for a in db.get_assignments(student_id=r['user_id']) if a['test_id'] == r['test_id']],
    })

# ===== Rubric Scoring (Speaking/Writing) =====

@app.route('/api/rubric-score/<int:result_id>', methods=['POST'])
@require_login
def api_save_rubric_score(result_id):
    """Save a rubric score for a speaking/writing question."""
    u = cur_user()
    r = db.get_result_by_id(result_id)
    if not r: abort(404)
    # Only assigning teacher or admin can score
    if u['role'] == 'student': abort(403)
    if u['role'] == 'teacher':
        assignments = db.get_assignments(student_id=r['user_id'])
        if not any(a['teacher_id'] == u['id'] and a['test_id'] == r['test_id'] for a in assignments):
            abort(403)
    data = request.get_json()
    if not data: abort(400)
    qid = data.get('question_id', '')
    score = data.get('score')
    if score is None or not isinstance(score, (int, float)) or score < 0 or score > 5:
        return jsonify({'ok': False, 'error': 'Score must be 0-5'}), 400
    # Store as a special comment with _rubric_ prefix
    db.save_teacher_comment(u['id'], result_id, f'_rubric_{qid}', str(int(score)))
    return jsonify({'ok': True})

@app.route('/api/toefl-scores/<int:result_id>')
@require_login
def api_toefl_scores(result_id):
    """Calculate TOEFL 2026 1-6 band scores per section."""
    r = get_result_or_403(result_id)
    sections = parse_json(r['sections_json'])
    comments = db.get_teacher_comments(result_id)
    rubric_map = {}
    for key, val in comments.items():
        if key.startswith('_rubric_'):
            qid = key[8:]
            try: rubric_map[qid] = int(val['comment'])
            except Exception: pass
    section_bands = {}
    for sec in sections:
        s = sec.get('section', '')
        band = section_band(s, sec.get('details', []), rubric_map)
        if band is not None:
            section_bands.setdefault(s, []).append(band)
    result_bands = {}
    for s, bands in section_bands.items():
        result_bands[s] = round(sum(bands) / len(bands) * 2) / 2
    if result_bands:
        overall = round(sum(result_bands.values()) / len(result_bands) * 2) / 2
    else:
        overall = None
    return jsonify({'section_bands': result_bands, 'overall': overall, 'rubric_scores': rubric_map})

# ===== History (merged with Analytics for students) =====

@app.route('/history')
@require_login
def history_page():
    u = cur_user()
    page = request.args.get('page', 1, type=int)
    per_page = 50
    total = db.count_results(user_id=u['id'])
    results = db.get_results(user_id=u['id'], limit=per_page, offset=(page-1)*per_page)
    total_pages = max(1, (total + per_page - 1) // per_page)
    my_assignments = db.get_assignments(student_id=u['id'])
    assigned_test_ids = set(a['test_id'] for a in my_assignments)
    return render_template('history.html', results=results, user=u,
        page=page, total_pages=total_pages, assigned_test_ids=assigned_test_ids,
        view_user=u, show_analytics=True)

@app.route('/teacher/student/<int:uid>')
@require_login
@require_role('admin', 'teacher')
def teacher_student_view(uid):
    """View a student's history and analytics (same layout the student sees)."""
    u = cur_user()
    student = db.get_user(uid)
    if not student: abort(404)
    page = request.args.get('page', 1, type=int)
    per_page = 50
    total = db.count_results(user_id=uid)
    results = db.get_results(user_id=uid, limit=per_page, offset=(page-1)*per_page)
    total_pages = max(1, (total + per_page - 1) // per_page)
    assignments = db.get_assignments(student_id=uid)
    assigned_test_ids = set(a['test_id'] for a in assignments)
    # Compute incomplete assignments for the teacher view
    completed_keys = db.get_completed_test_keys(uid)
    tests = cached_scan()
    pending = [a for a in assignments if (a['test_id'], a.get('section')) not in completed_keys]
    return render_template('history.html', results=results, user=u,
        page=page, total_pages=total_pages, assigned_test_ids=assigned_test_ids,
        view_user=student, show_analytics=True, pending_assignments=pending, tests=tests,
        now_date=dt.utcnow().strftime('%Y-%m-%d'))

# ===== Notes API =====

@app.route('/api/notes/<int:result_id>', methods=['GET'])
@require_login
def api_get_notes(result_id):
    r = get_result_or_403(result_id)
    return jsonify(db.get_notes(cur_user()['id'], result_id))

@app.route('/api/notes/<int:result_id>', methods=['POST'])
@require_login
def api_save_note(result_id):
    r = get_result_or_403(result_id)
    data = request.get_json()
    if not data: abort(400)
    u = cur_user()
    db.save_note(u['id'], result_id, data.get('question_id', ''), data.get('note', ''))
    return jsonify({'ok': True})

# ===== Analytics =====

@app.route('/dashboard')
@require_login
def dashboard():
    u = cur_user()
    if u['role'] == 'student':
        return redirect('/history')  # Merged into History for students
    if u['role'] == 'parent':
        return redirect('/parent')
    students = db.list_users(role='student')
    return render_template('dashboard.html', user=u, students=students)

@app.route('/api/analytics/<int:uid>')
@require_login
def api_analytics(uid):
    u = cur_user()
    if u['role'] == 'student' and u['id'] != uid: abort(403)
    if u['role'] == 'parent':
        student = db.get_linked_student(u['id'])
        if not student or student['id'] != uid: abort(403)
    results = db.get_analytics(uid)
    score_history = []
    section_totals = {}
    section_correct = {}
    for r in results:
        if r['total_questions'] > 0:
            pct = round(r['total_correct'] / r['total_questions'] * 100)
            # Calculate band from auto-graded sections
            secs = parse_json(r['sections_json'])
            bands = []
            for sec in secs:
                s = sec.get('section', '')
                sc = sec.get('score', {})
                if s in ('reading', 'listening') and sc.get('total', 0) > 0:
                    band = lookup_band(_RL_BAND_TABLE, sc.get('correct', 0) / sc.get('total', 1) * 30)
                    bands.append(band)
            avg_band = round(sum(bands) / len(bands) * 2) / 2 if bands else None
            score_history.append({'date': r['date'][:10], 'pct': pct, 'band': avg_band, 'name': r['test_name'] or r['test_id']})
        sections = parse_json(r['sections_json'])
        for sec in sections:
            s = sec.get('section', 'unknown')
            sc = sec.get('score', {})
            section_totals[s] = section_totals.get(s, 0) + sc.get('total', 0)
            section_correct[s] = section_correct.get(s, 0) + sc.get('correct', 0)
    # Always show all 4 sections
    section_breakdown = []
    for s in ['reading', 'listening', 'writing', 'speaking']:
        t = section_totals.get(s, 0)
        c = section_correct.get(s, 0)
        pct = round(c/t*100) if t > 0 else 0
        band = None
        if t > 0:
            if s in ('reading', 'listening'):
                band = lookup_band(_RL_BAND_TABLE, c / t * 30)
            elif s == 'writing':
                band = lookup_band(_WRITING_BAND_TABLE, c / t * 20)
            # speaking: no auto-graded questions, band requires rubric scores
        section_breakdown.append({'section': s, 'correct': c, 'total': t, 'pct': pct, 'band': band})
    return jsonify({'score_history': score_history, 'section_breakdown': section_breakdown})

# ===== Teacher Comments =====

@app.route('/api/comments/<int:result_id>', methods=['GET'])
@require_login
def api_get_comments(result_id):
    r = get_result_or_403(result_id)
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
    tests = cached_scan()
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
                parsed = cached_parse(fp)
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

# ===== PDF =====

@app.route('/api/export-pdf/<int:result_id>')
@require_login
def export_pdf_by_result(result_id):
    """Generate PDF from a saved result (server-side, no client data needed)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    try: pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light')); cjk = 'STSong-Light'
    except Exception: cjk = 'Helvetica'
    r = get_result_or_403(result_id)
    sections = parse_json(r['sections_json'])
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=25*mm, rightMargin=25*mm, topMargin=20*mm, bottomMargin=20*mm)
    ts = ParagraphStyle(name='T2', fontName='Helvetica-Bold', fontSize=18, leading=22, spaceAfter=4)
    ss = ParagraphStyle(name='Sub', fontName='Helvetica', fontSize=10, textColor=HexColor('#8e8e93'), spaceAfter=12)
    sec_s = ParagraphStyle(name='Sec', fontName='Helvetica-Bold', fontSize=13, leading=16, spaceBefore=16, spaceAfter=8)
    story = []
    story.append(Paragraph(r['test_name'] or r['test_id'], ts))
    parts = [fmtdate(r['date'])]
    parts.append(r.get('display_name') or r.get('username') or '')
    if r['practice']: parts.append('<font color="#ff9500"><b>PRACTICE</b></font>')
    story.append(Paragraph(' &bull; '.join(p for p in parts if p), ss))
    story.append(HRFlowable(width='100%', thickness=0.5, color=HexColor('#c6c6c8')))
    story.append(Spacer(1, 6*mm))
    gn, rd, mu = HexColor('#34c759'), HexColor('#ff3b30'), HexColor('#8e8e93')
    for sec in sections:
        sc = sec.get('score',{}); c_ = sc.get('correct',0); t_ = sc.get('total',0)
        stxt = f"{sec.get('section','').capitalize()} — Module {sec.get('moduleNum',1)}"
        if t_>0: stxt += f"  ({c_}/{t_}, {round(c_/t_*100)}%)"
        story.append(Paragraph(stxt, sec_s))
        rows = [['Q','','Your Answer','Correct','Time']]
        for d in sec.get('details',[]):
            q=str(d.get('qid','')); dt=d.get('type',''); tm=str(d.get('time',0))+'s' if d.get('time') else ''
            if dt in ('mc','cloze','build_sentence'):
                rows.append([q,'\u2713' if d.get('correct') else '\u2717',str(d.get('user',''))[:40],str(d.get('fullWord') or d.get('expected',''))[:40],tm])
            elif dt in ('email','discussion'):
                rows.append([q,'\u270E',f"{dt} ({d.get('wordCount',0)} words)",'',tm])
            elif dt in ('listen_repeat','interview'):
                rows.append([q,'\U0001F3A4' if d.get('hasRecording') else '\u2014','Recorded' if d.get('hasRecording') else 'No recording','',tm])
        if len(rows)>1:
            tbl = Table(rows, colWidths=[30,18,170,170,40], repeatRows=1)
            sty = [('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),9),
                ('TEXTCOLOR',(0,0),(-1,0),mu),('LINEBELOW',(0,0),(-1,0),0.5,HexColor('#c6c6c8')),
                ('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3),('VALIGN',(0,0),(-1,-1),'TOP')]
            for i,d in enumerate(sec.get('details',[]),1):
                if d.get('correct') is True: sty.append(('TEXTCOLOR',(1,i),(1,i),gn))
                elif d.get('correct') is False: sty.append(('TEXTCOLOR',(1,i),(1,i),rd))
            tbl.setStyle(TableStyle(sty)); story.append(tbl)
        story.append(Spacer(1,4*mm))
    doc.build(story); buf.seek(0)
    fname = (r['test_name'] or r['test_id'] or 'result') + '_report.pdf'
    return send_file(buf, mimetype='application/pdf', download_name=fname, as_attachment=True)

# ===== Error Bank (Spaced Repetition) =====

def _populate_error_bank(user_id, test_id, sections, tests_map):
    """Auto-populate error bank from wrong auto-graded answers.
    Also removes questions the student got right (they've learned it)."""
    # Build question data lookup from parsed test
    question_data_map = {}
    if test_id in tests_map:
        t = tests_map[test_id]
        seen = set()
        for mi in t['modules']:
            fn = mi['filename']
            if fn in seen: continue
            seen.add(fn)
            fp = safe_path(TESTS_DIR, fn)
            if fp and os.path.exists(fp):
                parsed = cached_parse(fp)
                for mod in parsed['modules']:
                    for pg in build_question_list(mod):
                        qid = str(pg.get('question_id', ''))
                        qt = pg.get('question_type', '')
                        if qt == 'mc':
                            question_data_map[qid] = {
                                'type': 'mc', 'prompt': pg.get('prompt', ''),
                                'choices': pg.get('choices', {}), 'answer': pg.get('answer', ''),
                                'passage': pg.get('passage', '')[:500],  # Truncate for storage
                            }
                        elif qt == 'cloze':
                            fills = pg.get('cloze_fills', [])
                            words = pg.get('cloze_answers', [])
                            for i, ef in enumerate(fills):
                                cqid = f'{qid}.{i+1}'
                                question_data_map[cqid] = {
                                    'type': 'cloze', 'fill': ef,
                                    'full_word': words[i] if i < len(words) else ef,
                                    'passage': pg.get('passage', '')[:500],
                                }
                        elif qt == 'build_sentence':
                            question_data_map[qid] = {
                                'type': 'build_sentence',
                                'details': pg.get('details', {}),
                                'answer': pg.get('answer', ''),
                            }
    # Process graded details — collect batch operations
    to_add = []
    to_remove = []
    for sec in sections:
        for d in sec.get('details', []):
            qid = str(d.get('qid', ''))
            dt = d.get('type', '')
            if dt not in ('mc', 'cloze', 'build_sentence'):
                continue
            if d.get('correct') is True:
                to_remove.append(qid)
            elif d.get('correct') is False:
                qdata = question_data_map.get(qid, {})
                if qdata:
                    correct_ans = d.get('expected', '') or d.get('fullWord', '') or qdata.get('answer', '')
                    to_add.append((qid, dt, json.dumps(qdata), correct_ans, str(d.get('user', ''))))
    if to_add or to_remove:
        db.batch_update_error_bank(user_id, test_id, to_add, to_remove)


@app.route('/review-queue')
@require_login
def review_queue_page():
    """Spaced repetition review page for students."""
    u = cur_user()
    if u['role'] not in ('student',):
        return redirect('/')
    return render_template('review_queue.html', user=u)


@app.route('/api/review-queue')
@require_login
def api_review_queue():
    """Get questions due for review."""
    u = cur_user()
    items = db.get_review_queue(u['id'])
    # Parse question_data_json for each item
    for item in items:
        try:
            item['question_data'] = json.loads(item['question_data_json'])
        except Exception:
            item['question_data'] = {}
        item.pop('question_data_json', None)
    return jsonify(items)


@app.route('/api/review-count')
@require_login
def api_review_count():
    """Get count of questions due for review (for badge)."""
    return jsonify({'count': db.get_review_count(cur_user()['id'])})


@app.route('/api/review-answer/<int:error_id>', methods=['POST'])
@require_login
def api_review_answer(error_id):
    """Submit an answer for a review question."""
    u = cur_user()
    data = request.get_json()
    if not data: abort(400)
    correct = data.get('correct', False)
    db.answer_review(error_id, u['id'], correct)
    return jsonify({'ok': True})


# ===== Live Monitoring =====

@app.route('/api/live-sessions')
@require_login
@require_role('admin', 'teacher')
def api_live_sessions():
    """Get active test sessions for teacher monitoring."""
    sessions = db.get_active_sessions_for_monitoring()
    tests = cached_scan()
    result = []
    for s in sessions:
        playlist = parse_json(s.get('playlist_json'))
        total_pages = 0
        current_module_name = ''
        if playlist and s['playlist_idx'] < len(playlist):
            current_mod = playlist[s['playlist_idx']]
            current_module_name = f"{current_mod.get('section', '').capitalize()} M{current_mod.get('module', 1)}"
            # Estimate total pages for current module
            fn = current_mod.get('filename', '')
            mi = current_mod.get('module_index', 0)
            fp = safe_path(TESTS_DIR, fn) if fn else None
            if fp and os.path.exists(fp):
                try:
                    parsed = cached_parse(fp)
                    if mi < len(parsed['modules']):
                        total_pages = len(build_question_list(parsed['modules'][mi]))
                except Exception:
                    pass
        test_name = tests.get(s['test_id'], {}).get('test_name', s['test_id'])
        result.append({
            'student_name': s.get('display_name') or s.get('username', ''),
            'test_name': test_name,
            'module': current_module_name,
            'current_page': s['current_page'] + 1,  # 1-indexed for display
            'total_pages': total_pages,
            'timer_left': s['timer_left'],
            'practice': bool(s['practice']),
            'updated_at': s['updated_at'],
        })
    return jsonify(result)


# ===== Parent Role =====

@app.route('/parent')
@require_login
def parent_home():
    """Parent landing page — redirect to child's history."""
    u = cur_user()
    if u['role'] != 'parent':
        return redirect('/')
    student = db.get_linked_student(u['id'])
    if not student:
        flash('No student linked to your account. Please contact the administrator.')
        return render_template('parent_no_student.html', user=u)
    return redirect(f'/parent/student/{student["id"]}')


@app.route('/parent/student/<int:student_id>')
@require_login
def parent_student_view(student_id):
    """Parent views their child's history and analytics."""
    u = cur_user()
    if u['role'] != 'parent':
        return redirect('/')
    student = db.get_linked_student(u['id'])
    if not student or student['id'] != student_id:
        abort(403)
    page = request.args.get('page', 1, type=int)
    per_page = 50
    total = db.count_results(user_id=student_id)
    results = db.get_results(user_id=student_id, limit=per_page, offset=(page-1)*per_page)
    total_pages = max(1, (total + per_page - 1) // per_page)
    assignments = db.get_assignments(student_id=student_id)
    assigned_test_ids = set(a['test_id'] for a in assignments)
    return render_template('history.html', results=results, user=u,
        page=page, total_pages=total_pages, assigned_test_ids=assigned_test_ids,
        view_user=student, show_analytics=True, is_parent_view=True)


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
