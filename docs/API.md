# API Reference — 超能录 (Chaonenglu) TOEFL Server

Complete reference for native clients (macOS / iOS / CLI / other services) accessing
the server via HTTP JSON over Bearer token authentication.

---

## Base URLs

| Environment | URL |
| --- | --- |
| Local dev (same machine) | `http://127.0.0.1:8080` |
| Local dev (LAN) | `http://<laptop-ip>:8080` |
| Production | `https://toefl.example.com` |

All examples below use `$BASE` to mean the chosen base URL.

> **Security:** use HTTPS for anything reachable from the public internet. Bearer
> tokens are long-lived credentials — never transmit them over plain HTTP in
> production.

---

## Authentication

### Model

- The client exchanges **username + password** for a long-lived **Bearer token** *once*.
- The token is stored by the client (Keychain on macOS).
- Every subsequent request carries `Authorization: Bearer <token>`.
- The server stores only a SHA-256 hash of the token — a DB leak can't expose it.
- The token persists across server restarts and can be revoked at any time.

### Headers

Two equivalent ways to present the token (the first is standard; the second is a
fallback for middleboxes that strip the `Authorization` header):

```
Authorization: Bearer tfl_<43-char-base64url>
```

or

```
X-API-Token: tfl_<43-char-base64url>
```

All token format is `tfl_` prefix + 43-character URL-safe base64 (256 bits of
entropy).

### Endpoints

#### `POST /api/auth/token`

Exchange credentials for a Bearer token. Rate-limited per IP (10 attempts / 5 min).

**Request body:**
```json
{
  "username": "jacques",
  "password": "...",
  "name": "Jacques' MacBook",
  "expires_in_days": 365
}
```

| Field | Required | Notes |
| --- | --- | --- |
| `username` | yes | |
| `password` | yes | |
| `name` | no | Human label shown in `/api/auth/tokens`. Max 64 chars. |
| `expires_in_days` | no | Integer. Omit for a non-expiring token. |

**Response 200:**
```json
{
  "ok": true,
  "token": "tfl_RmZerr8QV-LNQsmx_...",
  "token_id": 1,
  "expires_at": "2027-04-18 12:00:00",
  "user": {"id": 3, "username": "jacques", "display_name": "Jacques", "role": "admin"}
}
```

**Errors:**
- `401 {"ok": false, "error": "invalid_credentials"}`
- `429 {"ok": false, "error": "rate_limited"}` — wait 5 minutes.

The `token` value is shown **only in this response**. The server does not store
the plaintext and cannot re-display it.

#### `GET /api/auth/me`

Return the authenticated user. Works with either session cookie or Bearer token.

Used by clients to validate that a stored token is still good.

**Response 200:**
```json
{"ok": true, "user": {"id": 3, "username": "jacques", "display_name": "Jacques", "role": "admin"}}
```

**Response 401** if no valid auth: `{"ok": false, "error": "not_authenticated"}`.

#### `GET /api/auth/tokens`

List the authenticated user's tokens (plaintext values are **never** returned —
only metadata). Requires auth.

**Response:**
```json
{"ok": true, "tokens": [
  {"id": 1, "name": "Jacques' MacBook", "created_at": "2026-04-18 10:00:00",
   "last_used_at": "2026-04-18 12:30:00", "expires_at": null, "revoked": 0}
]}
```

#### `DELETE /api/auth/tokens/<id>`

Revoke one of your tokens. Requires auth.

**Response:** `{"ok": true}` (or `{"ok": false}` if the id isn't yours).

### Example: getting a token from the terminal

```bash
curl -sX POST $BASE/api/auth/token \
  -H 'Content-Type: application/json' \
  -d '{"username":"jacques","password":"...","name":"curl-test"}' \
  | jq -r .token
```

Then use it:

```bash
TOKEN="tfl_..."
curl -s $BASE/api/auth/me -H "Authorization: Bearer $TOKEN"
```

---

## Data endpoints

All require a valid Bearer token (or session cookie — the server accepts both).

### Catalog & assignments

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/catalog` | List all tests and their modules. |
| `GET` | `/api/my-assignments` | Current student's pending assignments. |
| `GET` | `/api/my-history` | Current user's completed test results, paginated (`?page=N`). |

#### `GET /api/catalog`
```json
{"ok": true, "tests": [
  {"test_id": "new-toefl-practice-1",
   "test_name": "2026 New TOEFL Practice Test 1",
   "sections": ["reading", "listening", "writing", "speaking"],
   "total_minutes": 110,
   "modules": [
     {"section": "reading", "module": 1, "timer_minutes": 35, "filename": "new-toefl-practice-1.md", "module_index": 0},
     ...
   ]}
]}
```

#### `GET /api/my-assignments`
```json
{"ok": true, "assignments": [
  {"id": 42, "test_id": "new-toefl-practice-1", "test_name": "...",
   "section": null, "due_date": "2026-04-25", "assigned_at": "2026-04-15 10:00:00",
   "schedule_start": null, "schedule_end": null,
   "sections": ["reading", "listening", "writing", "speaking"],
   "modules": [...]}
]}
```

#### `GET /api/my-history?page=1`
```json
{"ok": true, "page": 1, "total_pages": 3, "results": [
  {"id": 101, "test_id": "new-toefl-practice-1", "test_name": "...",
   "practice": false, "date": "2026-04-17 08:00:00",
   "total_correct": 78, "total_questions": 90,
   "band_overall": 5.0, "band_sections": {"reading": 5.5, "listening": 5.0},
   "needs_rubric": false}
]}
```

### Test session lifecycle

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/api/session/start` | Start or resume a session. Body: `{test_id, mode, section?, practice, playlist}`. |
| `POST` | `/api/session/<sid>/save` | Save in-progress state (answers, page, timer). |
| `GET` | `/api/session/<sid>` | Load full session state. |
| `POST` | `/api/session/<sid>/advance` | Advance to next module after grading. |
| `DELETE` | `/api/session/<sid>` | Abandon a session. |
| `POST` | `/api/session/<sid>/upload-recording` | Upload speaking recordings (multipart). |

### Grading & results

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/module/<filename>?module_index=N` | Fetch questions for one module. Never returns answer keys. |
| `POST` | `/api/grade` | Submit answers for one module, receive score. |
| `POST` | `/api/save-results` | Commit final result to history. |
| `GET` | `/api/review-data/<result_id>` | Full review content (questions, user answers, correct answers, comments, explanations, rubric scores). |
| `GET` | `/api/toefl-scores/<result_id>` | 1–6 band scores per section. |
| `GET` | `/api/analytics/<user_id>` | Score trend + section breakdown. Students only see themselves. |
| `GET` | `/api/export-pdf/<result_id>` | Download PDF report (binary response). |

### Review queue (spaced repetition)

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/review-queue` | Items due today. |
| `GET` | `/api/review-count` | Count only (for badges). |
| `POST` | `/api/review-answer/<error_id>` | Submit a review answer; server updates interval. |

### Notes & comments

| Method | Path | Description |
| --- | --- | --- |
| `GET`/`POST` | `/api/notes/<result_id>` | Student's own notes. |
| `GET`/`POST` | `/api/comments/<result_id>` | Teacher comments (teacher/admin only for POST). |
| `GET`/`POST` | `/api/explanations/<test_id>` | Question explanations (teacher/admin only for POST). |
| `POST` | `/api/rubric-score/<result_id>` | Save a rubric draft. Teacher/admin only. |
| `POST` | `/api/rubric-submit/<result_id>` | Publish drafts to student. Teacher/admin only. |

### Notifications

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/notifications` | Unread notifications + count. |
| `POST` | `/api/notifications/read` | Mark as read. Body: `{ids: [1,2,...]}` or `{}` for all. |

### Teacher/admin monitoring

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/live-sessions` | Active test sessions (teachers + admins only). |

---

## Errors

All errors are JSON when requested with `Authorization: Bearer` or `X-API-Token`:

| Status | Body | Meaning |
| --- | --- | --- |
| `400` | `{"error": ...}` | Malformed request. |
| `401` | `{"ok": false, "error": "not_authenticated"}` | Missing/invalid/revoked/expired token. Re-auth. |
| `403` | (empty or JSON) | Role insufficient, CSRF failure, or resource owned by another user. |
| `404` | (empty) | Resource doesn't exist. |
| `429` | `{"ok": false, "error": "rate_limited"}` | Login rate limit. |

---

## Running locally (same machine)

```bash
# 1. Start the server
cd toefl-practice-system
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py -p 8080     # or: gunicorn -c gunicorn.conf.py app:app

# 2. From another terminal, bootstrap a token
curl -sX POST http://127.0.0.1:8080/api/auth/token \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin","name":"dev"}'
```

The default admin account is `admin / admin` — change this immediately in
production by setting `default_admin` in `config.yaml` or by logging in and
changing the password via `/account`.

---

## Deploying publicly

### Recommended stack

`gunicorn` (WSGI) → `nginx` (TLS + reverse proxy) → public internet.

### Steps

```bash
# 1. Create a system user and layout
sudo useradd --system --shell /usr/sbin/nologin toefl
sudo mkdir -p /opt/toefl/{app,data,tests}
sudo chown -R toefl:toefl /opt/toefl

# 2. Deploy the code
sudo -u toefl bash -c '
  git clone <your-repo> /opt/toefl/app  # or rsync
  python3 -m venv /opt/toefl/venv
  /opt/toefl/venv/bin/pip install -r /opt/toefl/app/requirements.txt
'

# 3. Configure environment (edit deploy/toefl.service first)
sudo cp /opt/toefl/app/deploy/toefl.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now toefl
sudo systemctl status toefl

# 4. Set up nginx + TLS
sudo cp /opt/toefl/app/deploy/nginx.conf /etc/nginx/sites-available/toefl
sudo ln -s /etc/nginx/sites-available/toefl /etc/nginx/sites-enabled/
# Edit server_name to your domain first
sudo certbot --nginx -d toefl.example.com
sudo nginx -t && sudo systemctl reload nginx
```

### Required environment variables

| Variable | Required | Example | Notes |
| --- | --- | --- | --- |
| `SECRET_KEY` | **Yes (prod)** | `python3 -c "import secrets; print(secrets.token_hex(32))"` | Without this, sessions die on every restart. |
| `TOEFL_BEHIND_HTTPS` | Yes (prod) | `1` | Enables `Secure` cookie flag + honors `X-Forwarded-Proto`. |
| `TOEFL_DB_PATH` | No | `/opt/toefl/data/toefl.db` | |
| `TOEFL_TESTS_DIR` | No | `/opt/toefl/tests` | |
| `GUNICORN_BIND` | No | `127.0.0.1:8080` | |
| `GUNICORN_WORKERS` | No | `3` | Default: `2 × cores + 1`. |
| `CORS_ORIGINS` | No | `https://app.example.com` | Comma-separated. Enables CORS for browser clients at those origins. Native clients don't need this. |

### Firewall

Only expose ports 80 and 443. The gunicorn port (8080) should bind to loopback only.

```bash
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

### Database backups

SQLite in WAL mode is safe to copy while the server is running, but use the
`.backup` command rather than `cp` for atomic snapshots:

```bash
sqlite3 /opt/toefl/data/toefl.db ".backup /opt/toefl/backups/toefl-$(date +%F).db"
```

A daily cron entry is enough for a system this size.

---

## Moving from localhost to production

When you switch your macOS client from `http://127.0.0.1:8080` to `https://toefl.example.com`:

1. Old tokens you issued locally are in a different database — reissue against
   the new server.
2. If the user was already logged in via cookie on `localhost`, that session
   doesn't carry over. They'll need to re-auth.
3. macOS URLSession requires App Transport Security to allow HTTP in dev. For
   production HTTPS, no entitlements changes needed.

### App Transport Security exception for local development

To hit `http://127.0.0.1:8080` from a sandboxed macOS app during development,
add this to `Info.plist` (remove before release):

```xml
<key>NSAppTransportSecurity</key>
<dict>
    <key>NSAllowsLocalNetworking</key>
    <true/>
</dict>
```

`NSAllowsLocalNetworking` is preferable to `NSAllowsArbitraryLoads` because it
only whitelists RFC 1918 ranges and `*.local`, not the entire internet.
