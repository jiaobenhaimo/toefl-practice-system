# gunicorn.conf.py — production WSGI config for 超能录 TOEFL server.
#
# Run:  gunicorn -c gunicorn.conf.py app:app
# Or as a systemd service (see deploy/toefl.service).

import multiprocessing
import os

# Bind to loopback only — nginx (or another reverse proxy) terminates TLS
# and forwards. If you have no reverse proxy, bind to 0.0.0.0:PORT instead,
# but then you MUST front with HTTPS some other way (e.g. Cloudflare Tunnel).
bind = os.environ.get('GUNICORN_BIND', '127.0.0.1:8080')

# Workers: CPU-bound rule of thumb is 2×cores + 1. For an I/O-bound Flask
# app with SQLite this is fine; if you scale beyond one process you should
# also switch SQLite to its default journaling (already WAL here) and be
# aware that per-process caches (_parse_cache, _scan_cache, _expl_cache)
# are not shared across workers.
workers = int(os.environ.get('GUNICORN_WORKERS', multiprocessing.cpu_count() * 2 + 1))
threads = int(os.environ.get('GUNICORN_THREADS', 2))
worker_class = 'gthread'  # Threaded workers — plays well with SQLite in WAL mode.

# Client timeouts: a full test submit with recordings can be large.
timeout = 120
graceful_timeout = 30
keepalive = 5

# Max request size is enforced in Flask (MAX_CONTENT_LENGTH=16MB) too.
limit_request_line = 8190

# Logging — stdout/stderr so systemd journal captures them.
accesslog = '-'
errorlog = '-'
loglevel = os.environ.get('GUNICORN_LOGLEVEL', 'info')
# Combined-log format with forwarded-for
access_log_format = '%({x-forwarded-for}i)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Restart workers periodically to guard against memory leaks.
max_requests = 1000
max_requests_jitter = 100

# Preload the app to save memory (workers fork from a warm parent).
# Tradeoff: code changes require a full restart, not just HUP.
preload_app = True
