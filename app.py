"""
app.py — Flask application for TOEFL Practice Test System.
Auth, admin, teacher dashboards, server-side grading.
"""
import os, json, secrets, re, markdown, yaml
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

@app.context_processor
def inject_config():
    """Make site config and announcement available in all templates."""
    announcement = db.get_active_announcement()
    return {'site': SITE_CONFIG.get('site', {}), 'config': SITE_CONFIG, 'announcement': announcement}

TESTS_DIR = os.environ.get('TOEFL_TESTS_DIR',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tests'))

_md = markdown.Markdown(extensions=['tables', 'nl2br'])
_parse_cache = {}
_scan_cache = {'mtime': 0, 'count': -1, 'result': None}

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
    h = _md.convert(text); _md.reset(); return h

def pages_to_html(pages):
    for p in pages:
        for k in ('passage','prompt','content'):
            if k in p: p[k+'_html'] = md_html(p[k])
        if 'details' in p and 'context' in p['details']:
            p['details']['context_html'] = md_html(p['details']['context'])
    return pages

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
        u = db.authenticate(request.form.get('username','').strip(), request.form.get('password',''))
        if u:
            session['user_id'] = u['id']
            session['role'] = u['role']
            session['display_name'] = u['display_name']
            if request.form.get('remember') == 'on': session.permanent = True
            return redirect(request.args.get('next', '/'))
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
    return render_template('assignments.html', assignments=my_assignments, tests=tests, user=u)

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
    # Strip answers for client
    client_pages = []
    for p in pages:
        cp = dict(p)
        cp.pop('answer', None); cp.pop('cloze_answers', None); cp.pop('cloze_fills', None)
        client_pages.append(cp)
    return jsonify({
        'header': parsed['header'],
        'module_info': {'section': mod['section'], 'module': mod['module'], 'timer_minutes': mod['timer_minutes']},
        'pages': client_pages,
    })

@app.route('/api/grade', methods=['POST'])
def api_grade():
    """Server-side grading."""
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
    return jsonify({'section':mod['section'],'moduleNum':mod['module'],'score':{'correct':correct,'total':total},'details':details})

@app.route('/api/save-results', methods=['POST'])
def api_save_results():
    u = cur_user()
    if not u: return jsonify({'ok':False}), 401
    data = request.get_json()
    if not data: abort(400)
    db.save_result(u['id'], data.get('test_id',''), data.get('test_name',''),
        data.get('practice',False), data.get('total_correct',0),
        data.get('total_questions',0), json.dumps(data.get('sections',[])))
    return jsonify({'ok':True})

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
    db.update_user(uid, display_name=request.form.get('display_name'),
        password=pw or None, role=request.form.get('role'))
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
        for row in reader:
            un = (row.get('username') or '').strip()
            pw = (row.get('password') or '').strip()
            if un and pw:
                users.append({
                    'username': un, 'password': pw,
                    'role': (row.get('role') or 'student').strip(),
                    'display_name': (row.get('display_name') or un).strip(),
                })
        if not users: flash('No valid rows found in CSV'); return redirect('/admin/users')
        created, errors = db.bulk_create_users(users)
        msg = f'{created} user(s) created'
        if errors: msg += f', {len(errors)} error(s): ' + '; '.join(errors[:3])
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
    return render_template('teacher_results.html', results=db.get_results(),
        students=db.list_users(role='student'), tests=_cached_scan(), user=cur_user())

@app.route('/teacher/assign', methods=['POST'])
@require_login
@require_role('admin','teacher')
def teacher_assign():
    sid = request.form.get('student_id', type=int)
    tid = request.form.get('test_id','')
    sec = request.form.get('section','').strip() or None
    if sid and tid: db.assign_test(cur_user()['id'], sid, tid, sec); flash('Test assigned')
    return redirect('/teacher/results')

@app.route('/teacher/progress')
@require_login
@require_role('admin','teacher')
def teacher_progress():
    students = db.list_users(role='student')
    progress_data = []
    for s in students:
        assignments = db.get_assignments(student_id=s['id'])
        results = db.get_results(user_id=s['id'])
        completed_keys = set()
        for r in results:
            if not r['practice']:
                completed_keys.add(r['test_id'])
        total = len(assignments)
        done = sum(1 for a in assignments if a['test_id'] in completed_keys)
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
        elif len(new_pw) < 1:
            flash('New password cannot be empty')
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

# ===== History =====

@app.route('/history')
@require_login
def history_page():
    u = cur_user()
    return render_template('history.html', results=db.get_results(user_id=u['id']), user=u)

# ===== Notes API =====

@app.route('/api/notes/<int:result_id>', methods=['GET'])
@require_login
def api_get_notes(result_id):
    u = cur_user()
    return jsonify(db.get_notes(u['id'], result_id))

@app.route('/api/notes/<int:result_id>', methods=['POST'])
@require_login
def api_save_note(result_id):
    u = cur_user()
    data = request.get_json()
    if not data: abort(400)
    db.save_note(u['id'], result_id, data.get('question_id', ''), data.get('note', ''))
    return jsonify({'ok': True})

# ===== PDF =====

@app.route('/api/export-pdf', methods=['POST'])
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
    except: cjk = 'Helvetica'
    data = request.get_json()
    if not data: abort(400)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=25*mm, rightMargin=25*mm, topMargin=20*mm, bottomMargin=20*mm)
    ts = ParagraphStyle(name='T2', fontName='Helvetica-Bold', fontSize=18, leading=22, spaceAfter=4)
    ss = ParagraphStyle(name='Sub', fontName='Helvetica', fontSize=10, textColor=HexColor('#666'), spaceAfter=12)
    sec_s = ParagraphStyle(name='Sec', fontName='Helvetica-Bold', fontSize=13, leading=16, spaceBefore=16, spaceAfter=8)
    bf = cjk if data.get('lang')=='zh' else 'Helvetica'
    story = []
    story.append(Paragraph(data.get('test_name','TOEFL Practice Test'), ts))
    parts = [(data.get('date','') or '')[:10]]
    if data.get('student_name'): parts.append(data['student_name'])
    if data.get('student_id'): parts.append('ID: '+data['student_id'])
    if data.get('practice'): parts.append('<font color="#c08a1e"><b>PRACTICE</b></font>')
    story.append(Paragraph(' &bull; '.join(parts), ss))
    story.append(HRFlowable(width='100%', thickness=0.5, color=HexColor('#d8dbe4')))
    story.append(Spacer(1, 6*mm))
    gn, rd, mu = HexColor('#1a9f5c'), HexColor('#d94452'), HexColor('#666')
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
                ('TEXTCOLOR',(0,0),(-1,0),mu),('LINEBELOW',(0,0),(-1,0),0.5,HexColor('#d8dbe4')),
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
