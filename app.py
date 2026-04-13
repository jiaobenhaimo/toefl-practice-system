"""
app.py - Flask application for TOEFL Practice Test System
"""

import os
import markdown
from flask import (
    Flask, render_template, jsonify, request,
    send_from_directory, abort
)
from parser import scan_tests_directory, parse_test_file, build_question_list

app = Flask(__name__)

TESTS_DIR = os.environ.get(
    'TOEFL_TESTS_DIR',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tests')
)

# Reusable markdown converter instance
_md_instance = markdown.Markdown(extensions=['tables', 'nl2br'])

# File-level cache: { filepath: (mtime, parsed_result) }
_parse_cache = {}
_scan_cache = {'mtime': 0, 'count': -1, 'result': None}
_module_api_cache = {}  # { (filepath, module_index): (mtime, response_dict) }


def _cached_parse(filepath):
    """Parse a test file with mtime-based caching."""
    mtime = os.path.getmtime(filepath)
    cached = _parse_cache.get(filepath)
    if cached and cached[0] == mtime:
        return cached[1]
    result = parse_test_file(filepath)
    _parse_cache[filepath] = (mtime, result)
    return result


def _cached_module_response(filepath, module_index):
    """Get the full module API response with caching (includes HTML conversion)."""
    mtime = os.path.getmtime(filepath)
    cache_key = (filepath, module_index)
    cached = _module_api_cache.get(cache_key)
    if cached and cached[0] == mtime:
        return cached[1]

    parsed = _cached_parse(filepath)
    if module_index >= len(parsed['modules']):
        return None

    module_data = parsed['modules'][module_index]
    pages = build_question_list(module_data)
    pages = convert_pages_md_to_html(pages)

    response = {
        'header': parsed['header'],
        'module_info': {
            'section': module_data['section'],
            'module': module_data['module'],
            'timer_minutes': module_data['timer_minutes'],
        },
        'pages': pages,
    }
    _module_api_cache[cache_key] = (mtime, response)
    return response


def _cached_scan():
    """Scan tests directory with mtime-based caching.
    Re-scans if any .md file is newer than the last scan or file count changed.
    """
    from pathlib import Path
    tests_path = Path(TESTS_DIR)
    if not tests_path.exists():
        return {}

    latest_mtime = 0
    file_count = 0
    for md_file in tests_path.glob('*.md'):
        file_count += 1
        mt = md_file.stat().st_mtime
        if mt > latest_mtime:
            latest_mtime = mt

    if (_scan_cache['result'] is not None
            and _scan_cache['mtime'] >= latest_mtime
            and _scan_cache['count'] == file_count):
        return _scan_cache['result']

    result = scan_tests_directory(TESTS_DIR)
    _scan_cache['mtime'] = latest_mtime
    _scan_cache['count'] = file_count
    _scan_cache['result'] = result
    return result


def safe_md_convert(text):
    """Convert markdown to HTML."""
    md = _md_instance
    html = md.convert(text)
    md.reset()
    return html


def convert_pages_md_to_html(pages):
    """Convert markdown fields in page list to HTML."""
    for page in pages:
        if 'passage' in page:
            page['passage_html'] = safe_md_convert(page['passage'])
        if 'prompt' in page:
            page['prompt_html'] = safe_md_convert(page['prompt'])
        if 'content' in page:
            page['content_html'] = safe_md_convert(page['content'])
        if 'details' in page and 'context' in page['details']:
            page['details']['context_html'] = safe_md_convert(page['details']['context'])
    return pages


def safe_path(base_dir, user_path):
    """Resolve a user-supplied path and ensure it stays within base_dir."""
    base = os.path.realpath(base_dir)
    target = os.path.realpath(os.path.join(base_dir, user_path))
    if not target.startswith(base + os.sep) and target != base:
        return None
    return target


@app.route('/')
def catalog():
    """Display the test catalog page."""
    return render_template('catalog.html', tests=_cached_scan())


@app.route('/api/tests')
def api_tests():
    """Return all available tests as JSON."""
    return jsonify(_cached_scan())


@app.route('/api/module/<filename>')
def api_module(filename):
    """
    Return parsed module data as JSON.
    Query param: module_index (default 0) — which module in a multi-module file.
    """
    filepath = safe_path(TESTS_DIR, filename)
    if not filepath or not os.path.exists(filepath):
        abort(404)

    module_index = request.args.get('module_index', 0, type=int)
    response = _cached_module_response(filepath, module_index)
    if response is None:
        abort(404, description=f"Module index {module_index} not found in {filename}")

    return jsonify(response)


@app.route('/test/<test_id>')
def take_test(test_id):
    """Render the test-taking page."""
    tests = _cached_scan()
    if test_id not in tests:
        abort(404)
    return render_template('test.html', test_info=tests[test_id])


@app.route('/audio/<path:filepath>')
def serve_audio(filepath):
    """Serve audio files from the tests directory."""
    full_path = safe_path(TESTS_DIR, filepath)
    if not full_path or not os.path.exists(full_path):
        abort(404)
    return send_from_directory(
        os.path.dirname(full_path),
        os.path.basename(full_path),
        mimetype='audio/ogg'
    )


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='TOEFL Practice Test Server')
    parser.add_argument('-p', '--port', type=int, default=8080, help='Port to run on (default: 8080)')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to (default: 0.0.0.0)')
    args = parser.parse_args()
    print(f'Starting server on http://{args.host}:{args.port}')
    app.run(debug=True, host=args.host, port=args.port)
