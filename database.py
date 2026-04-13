"""
database.py — SQLite database for users, results, and test assignments.
"""

import sqlite3
import os
import json
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
"""


def get_db():
    """Get a database connection."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(config=None):
    """Initialize database schema and create default accounts if needed."""
    conn = get_db()
    conn.executescript(SCHEMA)
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
    conn.close()
    if user and check_password_hash(user['password_hash'], password):
        return dict(user)
    return None


def get_user(user_id):
    """Get user by ID."""
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return dict(user) if user else None


def list_users(role=None):
    """List all users, optionally filtered by role."""
    conn = get_db()
    if role:
        rows = conn.execute("SELECT * FROM users WHERE role=? ORDER BY created_at DESC", (role,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM users ORDER BY role, created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_user(username, password, role, display_name=''):
    """Create a new user. Returns user ID or raises on duplicate."""
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, display_name) VALUES (?, ?, ?, ?)",
            (username, generate_password_hash(password), role, display_name or username)
        )
        conn.commit()
        uid = cur.lastrowid
    finally:
        conn.close()
    return uid


def update_user(user_id, display_name=None, password=None, role=None):
    """Update user fields."""
    conn = get_db()
    if display_name is not None:
        conn.execute("UPDATE users SET display_name=? WHERE id=?", (display_name, user_id))
    if password:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(password), user_id))
    if role:
        conn.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
    conn.commit()
    conn.close()


def delete_user(user_id):
    """Delete user and their results."""
    conn = get_db()
    conn.execute("DELETE FROM test_results WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM test_assignments WHERE student_id=? OR teacher_id=?", (user_id, user_id))
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()


# ===== Test results =====

def save_result(user_id, test_id, test_name, practice, total_correct, total_questions, sections_json):
    """Save a test result."""
    conn = get_db()
    conn.execute(
        "INSERT INTO test_results (user_id, test_id, test_name, practice, total_correct, total_questions, sections_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, test_id, test_name, 1 if practice else 0, total_correct, total_questions, sections_json)
    )
    conn.commit()
    conn.close()


def get_result_by_id(result_id):
    """Get a single result by ID."""
    conn = get_db()
    row = conn.execute(
        "SELECT r.*, u.display_name, u.username FROM test_results r LEFT JOIN users u ON r.user_id = u.id WHERE r.id=?",
        (result_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_results(user_id=None, test_id=None, limit=100):
    """Get test results, optionally filtered."""
    conn = get_db()
    query = "SELECT r.*, u.display_name, u.username FROM test_results r LEFT JOIN users u ON r.user_id = u.id WHERE 1=1"
    params = []
    if user_id is not None:
        query += " AND r.user_id=?"
        params.append(user_id)
    if test_id:
        query += " AND r.test_id=?"
        params.append(test_id)
    query += " ORDER BY r.date DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ===== Test assignments =====

def assign_test(teacher_id, student_id, test_id, section=None):
    """Assign a test (or section) to a student."""
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM test_assignments WHERE teacher_id=? AND student_id=? AND test_id=? AND section IS ?",
        (teacher_id, student_id, test_id, section)
    ).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO test_assignments (teacher_id, student_id, test_id, section) VALUES (?, ?, ?, ?)",
            (teacher_id, student_id, test_id, section)
        )
        conn.commit()
    conn.close()


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
    conn.close()
    return [dict(r) for r in rows]


def remove_assignment(assignment_id):
    """Remove a test assignment."""
    conn = get_db()
    conn.execute("DELETE FROM test_assignments WHERE id=?", (assignment_id,))
    conn.commit()
    conn.close()

# ===== Announcements =====

def get_active_announcement():
    """Get the most recent active announcement."""
    conn = get_db()
    row = conn.execute(
        "SELECT a.*, u.display_name FROM announcements a JOIN users u ON a.author_id=u.id "
        "WHERE a.active=1 ORDER BY a.created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
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
    conn.close()


def dismiss_announcement():
    """Deactivate all announcements."""
    conn = get_db()
    conn.execute("UPDATE announcements SET active=0")
    conn.commit()
    conn.close()


# ===== Student notes =====

def get_notes(user_id, result_id):
    """Get all notes for a result."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM student_notes WHERE user_id=? AND result_id=? ORDER BY question_id",
        (user_id, result_id)
    ).fetchall()
    conn.close()
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
    conn.close()


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
    conn.close()
    return created, errors
