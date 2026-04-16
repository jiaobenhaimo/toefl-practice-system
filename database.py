"""
database.py — SQLite database for users, results, and test assignments.
Thread-local connection reuse: one connection per thread, closed at request teardown.
"""

import sqlite3
import os
import json
import threading
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.environ.get('TOEFL_DB_PATH', os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'data', 'toefl.db'
))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'student',  -- admin, teacher, student
    display_name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS test_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    test_id TEXT NOT NULL,
    test_name TEXT NOT NULL DEFAULT '',
    practice INTEGER NOT NULL DEFAULT 0,
    date TEXT NOT NULL DEFAULT (datetime('now')),
    total_correct INTEGER NOT NULL DEFAULT 0,
    total_questions INTEGER NOT NULL DEFAULT 0,
    sections_json TEXT NOT NULL DEFAULT '[]',
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS test_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    test_id TEXT NOT NULL,
    section TEXT DEFAULT NULL,  -- NULL = full test, 'reading' = section only
    due_date TEXT DEFAULT NULL, -- ISO date, NULL = no deadline
    assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (teacher_id) REFERENCES users(id),
    FOREIGN KEY (student_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    author_id INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (author_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS student_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    result_id INTEGER NOT NULL,
    question_id TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (result_id) REFERENCES test_results(id),
    UNIQUE(user_id, result_id, question_id)
);

CREATE TABLE IF NOT EXISTS teacher_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id INTEGER NOT NULL,
    result_id INTEGER NOT NULL,
    question_id TEXT DEFAULT NULL,  -- NULL = overall comment on the result
    comment TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (teacher_id) REFERENCES users(id),
    FOREIGN KEY (result_id) REFERENCES test_results(id),
    UNIQUE(teacher_id, result_id, question_id)
);

CREATE TABLE IF NOT EXISTS question_explanations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    explanation TEXT NOT NULL DEFAULT '',
    author_id INTEGER DEFAULT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (author_id) REFERENCES users(id),
    UNIQUE(test_id, question_id)
);

CREATE TABLE IF NOT EXISTS test_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    test_id TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'full',
    section TEXT DEFAULT NULL,
    practice INTEGER NOT NULL DEFAULT 0,
    playlist_json TEXT NOT NULL DEFAULT '[]',
    playlist_idx INTEGER NOT NULL DEFAULT 0,
    answers_json TEXT NOT NULL DEFAULT '{}',
    current_page INTEGER NOT NULL DEFAULT 0,
    timer_left INTEGER NOT NULL DEFAULT 0,
    question_times_json TEXT NOT NULL DEFAULT '{}',
    completed_json TEXT NOT NULL DEFAULT '[]',
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS error_bank (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    test_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    question_type TEXT NOT NULL,          -- mc, cloze, build_sentence
    question_data_json TEXT NOT NULL,     -- Full question data to re-render
    correct_answer TEXT NOT NULL,         -- Correct answer string
    user_wrong_answer TEXT NOT NULL DEFAULT '',
    interval_days INTEGER NOT NULL DEFAULT 1,
    next_review TEXT NOT NULL DEFAULT (date('now', '+1 day')),
    times_reviewed INTEGER NOT NULL DEFAULT 0,
    times_correct INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id),
    UNIQUE(user_id, test_id, question_id)
);
"""


_local = threading.local()


def _connect():
    """Create a new SQLite connection with standard pragmas."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_db():
    """Get a thread-local database connection (reused within same thread/request).
    ~40 open/close cycles per page reduced to 1."""
    conn = getattr(_local, 'conn', None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    return conn


def close_conn():
    """Close the thread-local connection. Register as Flask teardown_appcontext."""
    conn = getattr(_local, 'conn', None)
    if conn is not None:
        conn.close()
        _local.conn = None


def init_db(config=None):
    """Initialize database schema and create default accounts if needed.
    Uses its own connection (runs at startup, outside request context)."""
    conn = _connect()
    conn.executescript(SCHEMA)
    # Migrate: add due_date to test_assignments if missing
    cols = [r[1] for r in conn.execute("PRAGMA table_info(test_assignments)").fetchall()]
    if 'due_date' not in cols:
        conn.execute("ALTER TABLE test_assignments ADD COLUMN due_date TEXT DEFAULT NULL")
    # Migrate: add linked_student_id to users for parent role
    user_cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if 'linked_student_id' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN linked_student_id INTEGER DEFAULT NULL")
    # Create default admin account if none exists
    admin = conn.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
    if not admin:
        admin_cfg = (config or {}).get('default_admin', {})
        un = admin_cfg.get('username', 'admin')
        pw = admin_cfg.get('password', 'admin')
        conn.execute(
            "INSERT INTO users (username, password_hash, role, display_name) VALUES (?, ?, 'admin', 'Administrator')",
            (un, generate_password_hash(pw))
        )
        print(f"Created default admin account: {un} / {pw}")
    # Create test student account if configured and not exists
    test_cfg = (config or {}).get('test_account')
    if test_cfg:
        un = test_cfg.get('username', 'student')
        existing = conn.execute("SELECT id FROM users WHERE username=?", (un,)).fetchone()
        if not existing:
            pw = test_cfg.get('password', 'student')
            conn.execute(
                "INSERT INTO users (username, password_hash, role, display_name) VALUES (?, ?, 'student', ?)",
                (un, generate_password_hash(pw), un.capitalize())
            )
            print(f"Created test student account: {un} / {pw}")
    conn.commit()
    conn.close()


# ===== User operations =====

def authenticate(username, password):
    """Check credentials, return user row or None."""
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if user and check_password_hash(user['password_hash'], password):
        return dict(user)
    return None


def get_user(user_id):
    """Get user by ID."""
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(user) if user else None


def list_users(role=None):
    """List all users, optionally filtered by role."""
    conn = get_db()
    if role:
        rows = conn.execute("SELECT * FROM users WHERE role=? ORDER BY created_at DESC", (role,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM users ORDER BY role, created_at DESC").fetchall()
    return [dict(r) for r in rows]


def create_user(username, password, role, display_name=''):
    """Create a new user. Returns user ID or raises on duplicate."""
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, role, display_name) VALUES (?, ?, ?, ?)",
        (username, generate_password_hash(password), role, display_name or username)
    )
    conn.commit()
    return cur.lastrowid


def update_user(user_id, display_name=None, password=None, role=None, username=None, linked_student_id=None):
    """Update user fields."""
    conn = get_db()
    if username is not None:
        conn.execute("UPDATE users SET username=? WHERE id=?", (username, user_id))
    if display_name is not None:
        conn.execute("UPDATE users SET display_name=? WHERE id=?", (display_name, user_id))
    if password:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(password), user_id))
    if role:
        conn.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
    if linked_student_id is not None:
        conn.execute("UPDATE users SET linked_student_id=? WHERE id=?", (linked_student_id, user_id))
    conn.commit()


def delete_user(user_id):
    """Delete user and their results, assignments, notes, comments, sessions, and error bank."""
    conn = get_db()
    conn.execute("DELETE FROM error_bank WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM test_sessions WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM teacher_comments WHERE teacher_id=?", (user_id,))
    conn.execute("DELETE FROM student_notes WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM test_results WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM test_assignments WHERE student_id=? OR teacher_id=?", (user_id, user_id))
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()


# ===== Test results =====

def save_result(user_id, test_id, test_name, practice, total_correct, total_questions, sections_json):
    """Save a test result. Returns the new result ID."""
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO test_results (user_id, test_id, test_name, practice, total_correct, total_questions, sections_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, test_id, test_name, 1 if practice else 0, total_correct, total_questions, sections_json)
    )
    result_id = cur.lastrowid
    conn.commit()
    return result_id


def get_result_by_id(result_id):
    """Get a single result by ID."""
    conn = get_db()
    row = conn.execute(
        "SELECT r.*, u.display_name, u.username FROM test_results r LEFT JOIN users u ON r.user_id = u.id WHERE r.id=?",
        (result_id,)
    ).fetchone()
    return dict(row) if row else None


def get_results(user_id=None, test_id=None, limit=50, offset=0):
    """Get test results, optionally filtered, with pagination (#6)."""
    conn = get_db()
    query = "SELECT r.*, u.display_name, u.username FROM test_results r LEFT JOIN users u ON r.user_id = u.id WHERE 1=1"
    params = []
    if user_id is not None:
        query += " AND r.user_id=?"
        params.append(user_id)
    if test_id:
        query += " AND r.test_id=?"
        params.append(test_id)
    query += " ORDER BY r.date DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def count_results(user_id=None, test_id=None):
    """Count total results for pagination."""
    conn = get_db()
    query = "SELECT COUNT(*) FROM test_results WHERE 1=1"
    params = []
    if user_id is not None:
        query += " AND user_id=?"
        params.append(user_id)
    if test_id:
        query += " AND test_id=?"
        params.append(test_id)
    count = conn.execute(query, params).fetchone()[0]
    return count


# ===== Test assignments =====

def assign_test(teacher_id, student_id, test_id, section=None, due_date=None):
    """Assign a test (or section) to a student with optional due date."""
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM test_assignments WHERE teacher_id=? AND student_id=? AND test_id=? AND section IS ?",
        (teacher_id, student_id, test_id, section)
    ).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO test_assignments (teacher_id, student_id, test_id, section, due_date) VALUES (?, ?, ?, ?, ?)",
            (teacher_id, student_id, test_id, section, due_date)
        )
        conn.commit()


def get_assignments(teacher_id=None, student_id=None):
    """Get test assignments."""
    conn = get_db()
    query = """SELECT a.*, u.display_name as student_name, u.username as student_username
               FROM test_assignments a JOIN users u ON a.student_id = u.id WHERE 1=1"""
    params = []
    if teacher_id:
        query += " AND a.teacher_id=?"
        params.append(teacher_id)
    if student_id:
        query += " AND a.student_id=?"
        params.append(student_id)
    query += " ORDER BY a.assigned_at DESC"
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def remove_assignment(assignment_id):
    """Remove a test assignment."""
    conn = get_db()
    conn.execute("DELETE FROM test_assignments WHERE id=?", (assignment_id,))
    conn.commit()


def get_completed_test_keys(user_id):
    """Get set of (test_id, section_or_None) tuples for completed non-practice results."""
    conn = get_db()
    rows = conn.execute(
        "SELECT test_id, sections_json FROM test_results WHERE user_id=? AND practice=0",
        (user_id,)
    ).fetchall()
    keys = set()
    for r in rows:
        keys.add((r['test_id'], None))
        try:
            secs = json.loads(r['sections_json']) if r['sections_json'] else []
        except Exception: secs = []
        for sec in secs:
            keys.add((r['test_id'], sec.get('section')))
    return keys


def get_all_progress_data(student_ids):
    """Batch-load assignments and completed keys for multiple students. Returns dict {uid: {assignments, completed_keys}}."""
    if not student_ids:
        return {}
    conn = get_db()
    placeholders = ','.join('?' * len(student_ids))
    # All assignments for these students
    assign_rows = conn.execute(
        f"SELECT a.*, u.display_name as student_name, u.username as student_username "
        f"FROM test_assignments a JOIN users u ON a.student_id = u.id "
        f"WHERE a.student_id IN ({placeholders}) ORDER BY a.assigned_at DESC",
        student_ids
    ).fetchall()
    # All non-practice results for these students (only need test_id + sections_json)
    result_rows = conn.execute(
        f"SELECT user_id, test_id, sections_json FROM test_results "
        f"WHERE user_id IN ({placeholders}) AND practice=0",
        student_ids
    ).fetchall()
    # Group by student
    data = {uid: {'assignments': [], 'completed_keys': set()} for uid in student_ids}
    for r in assign_rows:
        sid = r['student_id']
        if sid in data:
            data[sid]['assignments'].append(dict(r))
    for r in result_rows:
        uid = r['user_id']
        if uid not in data:
            continue
        data[uid]['completed_keys'].add((r['test_id'], None))
        try:
            secs = json.loads(r['sections_json']) if r['sections_json'] else []
        except Exception: secs = []
        for sec in secs:
            data[uid]['completed_keys'].add((r['test_id'], sec.get('section')))
    return data

# ===== Announcements =====

def get_active_announcement():
    """Get the most recent active announcement."""
    conn = get_db()
    row = conn.execute(
        "SELECT a.*, u.display_name FROM announcements a JOIN users u ON a.author_id=u.id "
        "WHERE a.active=1 ORDER BY a.created_at DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def create_announcement(author_id, content):
    """Create a new announcement (deactivates previous ones)."""
    conn = get_db()
    conn.execute("UPDATE announcements SET active=0 WHERE active=1")
    conn.execute(
        "INSERT INTO announcements (author_id, content) VALUES (?, ?)",
        (author_id, content)
    )
    conn.commit()


def dismiss_announcement():
    """Deactivate all announcements."""
    conn = get_db()
    conn.execute("UPDATE announcements SET active=0")
    conn.commit()


# ===== Student notes =====

def get_notes(user_id, result_id):
    """Get all notes for a result."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM student_notes WHERE user_id=? AND result_id=? ORDER BY question_id",
        (user_id, result_id)
    ).fetchall()
    return {r['question_id']: r['note'] for r in rows}


def save_note(user_id, result_id, question_id, note):
    """Save or update a note."""
    conn = get_db()
    conn.execute(
        "INSERT INTO student_notes (user_id, result_id, question_id, note) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, result_id, question_id) DO UPDATE SET note=excluded.note, updated_at=datetime('now')",
        (user_id, result_id, question_id, note)
    )
    conn.commit()


# ===== Bulk user import =====

def bulk_create_users(users_list):
    """Create multiple users. Returns (created_count, errors)."""
    conn = get_db()
    created = 0
    errors = []
    for u in users_list:
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, display_name) VALUES (?, ?, ?, ?)",
                (u['username'], generate_password_hash(u['password']), u.get('role', 'student'), u.get('display_name', u['username']))
            )
            created += 1
        except Exception as e:
            errors.append(f"{u['username']}: {e}")
    conn.commit()
    return created, errors


# ===== Teacher comments =====

def get_teacher_comments(result_id):
    """Get all teacher comments for a result. Returns dict with 'overall' and per-question keys."""
    conn = get_db()
    rows = conn.execute(
        "SELECT tc.*, u.display_name FROM teacher_comments tc JOIN users u ON tc.teacher_id=u.id "
        "WHERE tc.result_id=? ORDER BY tc.updated_at DESC",
        (result_id,)
    ).fetchall()
    result = {}
    for r in rows:
        key = r['question_id'] if r['question_id'] else '_overall'
        result[key] = {'comment': r['comment'], 'teacher': r['display_name'], 'updated_at': r['updated_at']}
    return result


def save_teacher_comment(teacher_id, result_id, question_id, comment):
    """Save or update a teacher comment. question_id=None for overall comment."""
    conn = get_db()
    conn.execute(
        "INSERT INTO teacher_comments (teacher_id, result_id, question_id, comment) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(teacher_id, result_id, question_id) DO UPDATE SET comment=excluded.comment, updated_at=datetime('now')",
        (teacher_id, result_id, question_id or None, comment)
    )
    conn.commit()


# ===== Question explanations =====

def get_explanations(test_id):
    """Get all explanations for a test. Returns dict {question_id: explanation}."""
    conn = get_db()
    rows = conn.execute(
        "SELECT question_id, explanation FROM question_explanations WHERE test_id=?",
        (test_id,)
    ).fetchall()
    return {r['question_id']: r['explanation'] for r in rows}


def save_explanation(test_id, question_id, explanation, author_id=None):
    """Save or update an explanation for a question."""
    conn = get_db()
    conn.execute(
        "INSERT INTO question_explanations (test_id, question_id, explanation, author_id) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(test_id, question_id) DO UPDATE SET explanation=excluded.explanation, author_id=excluded.author_id, updated_at=datetime('now')",
        (test_id, question_id, explanation, author_id)
    )
    conn.commit()


# ===== Analytics =====

def get_analytics(user_id):
    """Get analytics data for a student: score history, section breakdowns."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, test_id, test_name, practice, date, total_correct, total_questions, sections_json "
        "FROM test_results WHERE user_id=? AND practice=0 ORDER BY date ASC",
        (user_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ===== Test Sessions (server-side progress) =====

def create_session(user_id, test_id, mode, section, practice, playlist_json, timer_left):
    """Create a new test session. Returns session ID. Also cleans up stale sessions."""
    conn = get_db()
    # Clean up stale unfinished sessions older than 7 days
    conn.execute(
        "DELETE FROM test_sessions WHERE finished=0 AND updated_at < datetime('now', '-7 days')"
    )
    # Clean up finished sessions older than 30 days
    conn.execute(
        "DELETE FROM test_sessions WHERE finished=1 AND updated_at < datetime('now', '-30 days')"
    )
    cur = conn.execute(
        "INSERT INTO test_sessions (user_id, test_id, mode, section, practice, playlist_json, timer_left) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, test_id, mode, section, 1 if practice else 0, playlist_json, timer_left)
    )
    sid = cur.lastrowid
    conn.commit()
    return sid


def get_active_session(user_id, test_id, mode, section, practice):
    """Find an unfinished session for this user/test/mode combo."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM test_sessions WHERE user_id=? AND test_id=? AND mode=? "
        "AND section IS ? AND practice=? AND finished=0 ORDER BY updated_at DESC LIMIT 1",
        (user_id, test_id, mode, section, 1 if practice else 0)
    ).fetchone()
    return dict(row) if row else None


def get_session(session_id):
    """Get session by ID."""
    conn = get_db()
    row = conn.execute("SELECT * FROM test_sessions WHERE id=?", (session_id,)).fetchone()
    return dict(row) if row else None


def save_session_progress(session_id, answers_json, current_page, timer_left, question_times_json):
    """Update in-progress session state."""
    conn = get_db()
    conn.execute(
        "UPDATE test_sessions SET answers_json=?, current_page=?, timer_left=?, "
        "question_times_json=?, updated_at=datetime('now') WHERE id=?",
        (answers_json, current_page, timer_left, question_times_json, session_id)
    )
    conn.commit()


def advance_session(session_id, playlist_idx, completed_json, answers_json='{}', timer_left=0):
    """Advance to next module after grading."""
    conn = get_db()
    conn.execute(
        "UPDATE test_sessions SET playlist_idx=?, completed_json=?, answers_json=?, "
        "current_page=0, timer_left=?, question_times_json='{}', updated_at=datetime('now') WHERE id=?",
        (playlist_idx, completed_json, answers_json, timer_left, session_id)
    )
    conn.commit()


def finish_session(session_id):
    """Mark session as finished."""
    conn = get_db()
    conn.execute("UPDATE test_sessions SET finished=1, updated_at=datetime('now') WHERE id=?", (session_id,))
    conn.commit()


def delete_session(session_id):
    """Delete a session."""
    conn = get_db()
    conn.execute("DELETE FROM test_sessions WHERE id=?", (session_id,))
    conn.commit()


# ===== Error Bank (Spaced Repetition) =====

def add_to_error_bank(user_id, test_id, question_id, question_type, question_data_json, correct_answer, user_wrong_answer=''):
    """Add a wrong answer to the error bank. If already exists, resets interval to 1 day."""
    conn = get_db()
    conn.execute(
        "INSERT INTO error_bank (user_id, test_id, question_id, question_type, question_data_json, correct_answer, user_wrong_answer) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, test_id, question_id) DO UPDATE SET "
        "user_wrong_answer=excluded.user_wrong_answer, interval_days=1, "
        "next_review=date('now', '+1 day')",
        (user_id, test_id, question_id, question_type, question_data_json, correct_answer, user_wrong_answer)
    )
    conn.commit()


def remove_from_error_bank(user_id, test_id, question_id):
    """Remove a question from the error bank (student got it right on a real test)."""
    conn = get_db()
    conn.execute("DELETE FROM error_bank WHERE user_id=? AND test_id=? AND question_id=?",
        (user_id, test_id, question_id))
    conn.commit()


def batch_update_error_bank(user_id, test_id, to_add, to_remove):
    """Batch add/remove error bank items in a single transaction.
    to_add: list of (question_id, question_type, question_data_json, correct_answer, user_wrong_answer)
    to_remove: list of question_id strings
    """
    conn = get_db()
    for qid in to_remove:
        conn.execute("DELETE FROM error_bank WHERE user_id=? AND test_id=? AND question_id=?",
            (user_id, test_id, qid))
    for item in to_add:
        conn.execute(
            "INSERT INTO error_bank (user_id, test_id, question_id, question_type, question_data_json, correct_answer, user_wrong_answer) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, test_id, question_id) DO UPDATE SET "
            "user_wrong_answer=excluded.user_wrong_answer, interval_days=1, "
            "next_review=date('now', '+1 day')",
            (user_id, test_id, item[0], item[1], item[2], item[3], item[4])
        )
    conn.commit()


def get_review_queue(user_id, limit=20):
    """Get questions due for review (next_review <= today)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM error_bank WHERE user_id=? AND next_review <= date('now') "
        "ORDER BY next_review ASC, interval_days ASC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def get_review_count(user_id):
    """Count questions due for review today."""
    conn = get_db()
    count = conn.execute(
        "SELECT COUNT(*) FROM error_bank WHERE user_id=? AND next_review <= date('now')",
        (user_id,)
    ).fetchone()[0]
    return count


def answer_review(error_id, user_id, correct):
    """Update a review item after the student answers. Correct: advance interval. Wrong: reset to 1 day."""
    conn = get_db()
    row = conn.execute("SELECT * FROM error_bank WHERE id=? AND user_id=?", (error_id, user_id)).fetchone()
    if not row:
        return
    if correct:
        # Advance interval: 1 -> 3 -> 7 -> 14 -> done (remove)
        intervals = [1, 3, 7, 14]
        current = row['interval_days']
        idx = intervals.index(current) if current in intervals else 0
        if idx >= len(intervals) - 1:
            # Mastered — remove from bank
            conn.execute("DELETE FROM error_bank WHERE id=?", (error_id,))
        else:
            next_interval = intervals[idx + 1]
            conn.execute(
                "UPDATE error_bank SET interval_days=?, next_review=date('now', '+' || ? || ' days'), "
                "times_reviewed=times_reviewed+1, times_correct=times_correct+1 WHERE id=?",
                (next_interval, next_interval, error_id)
            )
    else:
        # Wrong — reset to 1 day
        conn.execute(
            "UPDATE error_bank SET interval_days=1, next_review=date('now', '+1 day'), "
            "times_reviewed=times_reviewed+1 WHERE id=?",
            (error_id,)
        )
    conn.commit()


# ===== Live Sessions (Teacher Monitoring) =====

def get_active_sessions_for_monitoring():
    """Get all active (unfinished) sessions with user info for teacher monitoring."""
    conn = get_db()
    rows = conn.execute(
        "SELECT s.*, u.display_name, u.username FROM test_sessions s "
        "JOIN users u ON s.user_id = u.id "
        "WHERE s.finished=0 AND s.updated_at > datetime('now', '-30 minutes') "
        "ORDER BY s.updated_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


# ===== Parent Role =====

def get_linked_student(parent_id):
    """Get the student linked to a parent account."""
    conn = get_db()
    parent = conn.execute("SELECT linked_student_id FROM users WHERE id=? AND role='parent'", (parent_id,)).fetchone()
    if not parent or not parent['linked_student_id']:
        return None
    student = conn.execute("SELECT * FROM users WHERE id=?", (parent['linked_student_id'],)).fetchone()
    return dict(student) if student else None


def get_parents_of_student(student_id):
    """Get all parent accounts linked to a student."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM users WHERE role='parent' AND linked_student_id=?",
        (student_id,)
    ).fetchall()
    return [dict(r) for r in rows]
