#!/usr/bin/env python3
"""Command-line client for a ObserveContext observability server. Python 3 standard library only.

Configuration comes from three environment variables:
  OBSERVECONTEXT_URL             server address, for example https://observe.example.com
  OBSERVECONTEXT_USER_EMAIL     email of an account in the `users` collection
  OBSERVECONTEXT_USER_PASSWORD  password of that account (optional with Google login)

Exit codes: 0 success; 1 HTTP or transport error; 2 usage or configuration error;
3 `check` found schema differences; 4 HTTP 409 (read the record again, then retry).
"""
import argparse
import base64
import hashlib
import http.client
import http.server
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ENV = ['OBSERVECONTEXT_URL', 'OBSERVECONTEXT_USER_EMAIL', 'OBSERVECONTEXT_USER_PASSWORD']
SCHEMA_FILE = Path(__file__).resolve().parent.parent / 'references' / 'schema.json'
STAMPS = ('created_by', 'updated_by')
ID_ALPHABET = 'abcdefghijklmnopqrstuvwxyz0123456789'
TIMEOUT = 30
USER_AGENT = 'ObserveContext/1.0'
hidden = []  # The password and tokens. say() masks them in everything it prints.


class Fail(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def hide(value):
    if value:
        hidden.append(value)
    return value


def say(text, stream=sys.stderr):
    for value in hidden:
        text = text.replace(value, '***')
    print(text, file=stream)


def dump(data, pretty=False):
    if pretty:
        return json.dumps(data, indent=2, ensure_ascii=False)
    return json.dumps(data, separators=(',', ':'), ensure_ascii=False)


def config(names=ENV[:2]):
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise Fail(2, 'missing environment variable: ' + ', '.join(missing) + '. Ask the user to set every missing variable; do not look for credentials elsewhere.')
    url = os.environ[ENV[0]].rstrip('/')
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise Fail(2, 'OBSERVECONTEXT_URL must be an HTTP(S) URL without credentials, query, or fragment')
    if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise Fail(2, 'Use HTTPS for a remote ObserveContext server')
    return {'url': url, 'email': os.environ[ENV[1]], 'password': hide(os.environ.get(ENV[2]))}


# Token cache: one file per server URL and email, readable only by the current user.

def cache_file(cfg):
    base = os.environ.get('XDG_CACHE_HOME') or str(Path.home() / '.cache')
    key = hashlib.sha256((cfg['url'] + '\n' + cfg['email']).encode()).hexdigest()[:32]
    return Path(base) / 'observecontext' / (key + '.json')


def load_session(cfg):
    try:
        session = json.loads(cache_file(cfg).read_text())
        if not isinstance(session, dict) or session.get('url') != cfg['url'] or session.get('email') != cfg['email']:
            return None
        return session if isinstance(session.get('token'), str) and hide(session['token']) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_session(cfg, session):
    path = cache_file(cfg)
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.session-', delete=False) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            json.dump(session, handle)
        os.replace(temporary, path)
    except OSError as error:
        say(f'note: token not cached ({error.strerror}); sign-in will be required again')


# HTTP

class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A followed redirect would turn a POST into a GET and could send the token to another host."""
    def redirect_request(self, *args):
        return None


opener = urllib.request.build_opener(NoRedirect)


def send(cfg, method, path, body=None, token=None, timeout=TIMEOUT):
    """Send one request. Returns (status, parsed JSON body, or the text when it is not JSON)."""
    headers = {'Content-Type': 'application/json', 'User-Agent': USER_AGENT}
    if token:
        headers['Authorization'] = token
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(cfg['url'] + path, data=data, headers=headers, method=method)
    try:
        with opener.open(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
        if 300 <= status < 400:
            raise Fail(1, f'HTTP {status}: the server redirects to {error.headers.get("Location")}. Set OBSERVECONTEXT_URL to the final address.')
    except (OSError, ValueError, http.client.HTTPException) as error:
        reason = getattr(error, 'reason', error)
        raise Fail(1, f'cannot reach {cfg["url"]}: {reason}')
    text = raw.decode('utf-8', 'replace')
    try:
        return status, json.loads(text) if text else None
    except ValueError:
        return status, text[:2000]


def login(cfg):
    if not cfg.get('password'):
        raise Fail(2, 'Set OBSERVECONTEXT_USER_PASSWORD for password login, or run oc.py login --google for browser sign-in.')
    status, data = send(cfg, 'POST', '/api/collections/users/auth-with-password', {'identity': cfg['email'], 'password': cfg['password']})
    if status != 200 or not isinstance(data, dict) or 'token' not in data:
        raise Fail(1, f'login as {cfg["email"]} failed: HTTP {status}\n{dump(data)}\nCheck the three OBSERVECONTEXT_ variables with the user. User credentials only.')
    session = {'url': cfg['url'], 'email': cfg['email'], 'token': hide(data['token'])}
    save_session(cfg, session)
    return session


def auth_session(cfg, data, method):
    """Accept only the expected users identity; never retain provider metadata."""
    token = data.get('token') if isinstance(data, dict) else None
    if isinstance(token, str):
        hide(token)
    record = data.get('record') if isinstance(data, dict) else None
    if (not isinstance(token, str) or not token or not isinstance(record, dict) or record.get('collectionName') != 'users'
            or not record.get('id') or not isinstance(record.get('email'), str)
            or record['email'].casefold() != cfg['email'].casefold()):
        raise Fail(1, 'Authentication returned an unexpected identity; no session saved. Check OBSERVECONTEXT_USER_EMAIL.')
    session = {'url': cfg['url'], 'email': cfg['email'], 'token': token, 'method': method, 'refreshed_at': time.time()}
    save_session(cfg, session)
    return session


def oauth_send(cfg, method, path, body=None, token=None):
    try:
        return send(cfg, method, path, body, token)
    except Fail:
        # Redirect locations and transport errors may contain authorization credentials.
        raise Fail(1, 'OAuth authentication request failed; check the server URL and connection, then retry.') from None


def oauth_refresh_needed(session):
    """Unverified JWT claims only schedule renewal; the server always authenticates the token."""
    try:
        payload = session['token'].split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        now = time.time()
        refreshed_at = session['refreshed_at']
        return (not 0 <= now - refreshed_at < 300 or claims['exp'] <= now + 60)
    except (ValueError, TypeError, KeyError, IndexError):
        return True


def google_login(cfg, port=8765, timeout=180):
    if not 1 <= port <= 65535 or not 1 <= timeout <= 600:
        raise Fail(2, 'OAuth port must be 1–65535 and timeout must be 1–600 seconds')
    status, data = oauth_send(cfg, 'GET', '/api/collections/users/auth-methods')
    oauth = data.get('oauth2', {}) if isinstance(data, dict) else {}
    providers = oauth.get('providers', [])
    provider = next((p for p in providers if isinstance(p, dict) and p.get('name') == 'google'), None)
    if status != 200 or not oauth.get('enabled') or not provider:
        raise Fail(1, 'Google OAuth is not enabled on this ObserveContext server.')
    auth_url = urllib.parse.urlsplit(provider.get('authURL', ''))
    if auth_url.scheme != 'https' or auth_url.hostname != 'accounts.google.com' or auth_url.username or auth_url.password or auth_url.fragment:
        raise Fail(1, 'Server returned an unexpected Google authorization URL.')
    state = secrets.token_urlsafe(32)
    verifier = hide(secrets.token_urlsafe(48))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    redirect = f'http://127.0.0.1:{port}/callback'
    metadata = urllib.parse.parse_qs(auth_url.query)
    client_ids = metadata.get('client_id', [])
    if len(client_ids) != 1 or not client_ids[0]:
        raise Fail(1, 'Server returned an invalid Google client ID.')
    params = {'client_id': client_ids[0]}
    params.update(state=state, code_challenge=challenge, code_challenge_method='S256', redirect_uri=redirect,
                  login_hint=cfg['email'], response_type='code', scope='openid email profile', access_type='online')
    url = urllib.parse.urlunsplit(auth_url._replace(query=urllib.parse.urlencode(params)))
    outcome = {}

    class Callback(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Callback URLs contain credentials.

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            values = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            code = values.get('code', [])
            valid_state = values.get('state', [])
            valid = (self.headers.get('Host') == f'127.0.0.1:{port}' and parsed.path == '/callback' and len(valid_state) == 1
                     and secrets.compare_digest(valid_state[0], state))
            if not valid:
                status, message = 400, 'Invalid sign-in callback. Return to your terminal.'
            elif 'error' in values:
                outcome['error'] = 'Google sign-in was denied or cancelled; run oc.py login --google to retry.'
                status, message = 400, 'Sign-in was cancelled. Return to your terminal.'
            elif len(code) != 1 or not code[0]:
                outcome['error'] = 'Google returned an invalid sign-in callback.'
                status, message = 400, 'Invalid sign-in callback. Return to your terminal.'
            else:
                outcome['code'] = hide(code[0])
                status, message = 200, 'Authorization received. Return to your terminal to check sign-in.'
            self.send_response(status)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(message.encode())

    class Listener(http.server.HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(1)
            return connection, address

        def handle_error(self, request, client_address):
            pass  # Never print request data or exception tracebacks.

    try:
        server = Listener(('127.0.0.1', port), Callback)
    except OSError:
        raise Fail(1, f'Cannot listen on 127.0.0.1:{port}; check for another login process or choose --port.')
    with server:
        server.timeout = 0.25
        say(f'For SSH, forward this port: ssh -L {port}:127.0.0.1:{port} user@ssh-host')
        say('Open this URL in your browser (keep it private):\n' + url)
        deadline = time.monotonic() + timeout
        while not outcome and time.monotonic() < deadline:
            server.handle_request()
    if not outcome:
        raise Fail(1, 'Google sign-in timed out; run oc.py login --google to retry.')
    if 'error' in outcome:
        raise Fail(1, outcome['error'])
    status, data = oauth_send(cfg, 'POST', '/api/collections/users/auth-with-oauth2', {
        'provider': 'google', 'code': outcome['code'], 'codeVerifier': verifier, 'redirectURL': redirect,
    })
    if status != 200:
        raise Fail(1, f'Google sign-in failed: HTTP {status}. Check Workspace eligibility, account access, and the redirect URI with your operator.')
    return auth_session(cfg, data, 'google')


def token_rejected(cfg, token):
    return send(cfg, 'POST', '/api/context/query', {'sql': 'SELECT 1'}, token)[0] == 401


def call(cfg, method, path, body=None):
    """Authenticated request. Returns (status, data).

    Only the SQL endpoints answer an expired or revoked token with 401. The records API treats it as no
    token and answers 400, 403, or 404. So after such an error with a cached token, check the token,
    and if the server rejects it, log in once and send the request once more. The first attempt wrote nothing.
    """
    session = load_session(cfg)
    cached = session is not None
    if not cached:
        session = login(cfg)
    if session.get('method') == 'google' and (oauth_refresh_needed(session) or path == '/api/collections/users/auth-refresh'):
        # Renew at most every five minutes, or near expiry, to respect auth rate limits.
        status, data = oauth_send(cfg, 'POST', '/api/collections/users/auth-refresh', token=session['token'])
        if status != 200:
            raise Fail(1, f'Google session could not be refreshed (HTTP {status}); run oc.py login --google again.')
        session = auth_session(cfg, data, 'google')
        if path == '/api/collections/users/auth-refresh':
            return status, data
    status, data = send(cfg, method, path, body, session['token'])
    if cached and 400 <= status < 500 and status != 409 and (status == 401 or token_rejected(cfg, session['token'])):
        if session.get('method') == 'google':
            raise Fail(1, 'Google session was rejected; run oc.py login --google again.')
        session = login(cfg)
        status, data = send(cfg, method, path, body, session['token'])
    return status, data


def batch_failures(data):
    """The failed requests of a rejected batch as (index, status, message). The server reports them under data.requests."""
    try:
        failures = []
        for index, entry in data['data']['requests'].items():
            response = entry['response']
            fields = [f'{name}: {detail.get("message")}' for name, detail in (response.get('data') or {}).items() if isinstance(detail, dict)]
            failures.append((index, response.get('status'), ' '.join([response.get('message') or ''] + fields)))
        return failures
    except (AttributeError, KeyError, TypeError):
        return []


def must(cfg, method, path, body=None):
    """Like call(), but an HTTP error ends the command with the server's status and body on stderr."""
    status, data = call(cfg, method, path, body)
    if status < 400:
        return data
    lines = [f'HTTP {status} from {method} {path}', dump(data, pretty=True)]
    conflict = status == 409
    if path == '/api/batch':
        lines.append('Nothing in this batch was saved.')
        for index, inner_status, message in batch_failures(data):
            lines.append(f'Failed request index {index}: HTTP {inner_status}: {message}')
            conflict = conflict or inner_status == 409
    if conflict:
        lines.append('HTTP 409: another request changed the record first. Read it again, confirm the change still applies, then retry.')
    raise Fail(4 if conflict else 1, '\n'.join(lines))


# Commands

def read_json(text, kind, what):
    if text == '-':
        text = sys.stdin.read()
    try:
        value = json.loads(text)
    except ValueError as error:
        raise Fail(2, f'{what} is not valid JSON: {error}')
    if not isinstance(value, kind):
        raise Fail(2, f'{what} must be a JSON {"array" if kind is list else "object"}')
    return value


def records(collection, record=None):
    path = f'/api/collections/{urllib.parse.quote(collection, safe="")}/records'
    return path if record is None else f'{path}/{urllib.parse.quote(record, safe="")}'


def columns(tables):
    return {table['name']: {column['name']: column.get('type') for column in table['columns']} for table in tables}


def check(cfg):
    """Compare the live SQL schema with references/schema.json. Returns the exit code."""
    try:
        reference = columns(json.loads(SCHEMA_FILE.read_text())['tables'])
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise Fail(2, f'cannot read {SCHEMA_FILE}: {error}')
    live = columns(must(cfg, 'GET', '/api/context/schema')['tables'])
    differences = []
    for table in sorted(set(live) | set(reference)):
        if table not in reference:
            differences.append(f'table {table}: on the server, not in the reference files')
        elif table not in live:
            differences.append(f'table {table}: in the reference files, not on the server')
        else:
            for column in sorted(set(live[table]) | set(reference[table])):
                if column not in reference[table]:
                    differences.append(f'column {table}.{column}: on the server, not in the reference files')
                elif column not in live[table]:
                    differences.append(f'column {table}.{column}: in the reference files, not on the server')
                elif live[table][column] != reference[table][column]:
                    differences.append(f'column {table}.{column}: type {live[table][column]} on the server, {reference[table][column]} in the reference files')
    if not differences:
        say(f'OK: the live schema matches references/schema.json ({len(live)} tables)', sys.stdout)
        return 0
    for line in differences:
        say(line, sys.stdout)
    say('The server is authoritative: run `oc.py schema` and follow the server\'s error messages where the reference files disagree. '
        'Ask the user to update this skill.', sys.stdout)
    return 3


TRACE_FIELDS = ('version', 'request_id', 'correlation_id', 'service', 'method', 'route',
                'started_at', 'duration_ms', 'status', 'user_id', 'sql', 'rows', 'truncated', 'spans')


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def query(cfg, sql):
    result = must(cfg, 'POST', '/api/context/query', {'sql': sql})
    if result.get('truncated'):
        raise Fail(1, 'SQL result truncated; narrow the time window or select fewer fields')
    return [dict(zip(result['columns'], row)) for row in result['rows']]


def canonical(event):
    import datetime
    defaults = {'sql':'', 'user_id':'', 'correlation_id':'', 'rows':0, 'truncated':False, 'spans':[]}
    fields = {key: event.get(key, defaults.get(key)) for key in TRACE_FIELDS}
    try:
        date = datetime.datetime.fromisoformat(fields['started_at'].replace('Z', '+00:00'))
        if date.tzinfo is None or date.utcoffset() is None:
            raise ValueError('timezone required')
        fields['started_at'] = date.astimezone(datetime.timezone.utc).isoformat(timespec='milliseconds')
    except (ValueError, TypeError, AttributeError):
        raise Fail(2, 'trace started_at must be an ISO timestamp with timezone') from None
    if isinstance(fields['spans'], str):
        fields['spans'] = json.loads(fields['spans'])
    fields['truncated'] = bool(fields['truncated'])
    return fields


def ingest_one(cfg, event):
    if not isinstance(event, dict) or event.get('version') != 1:
        raise Fail(2, 'expected a version 1 trace object')
    extra = set(event) - set(TRACE_FIELDS)
    if extra:
        raise Fail(2, 'unsupported trace fields: ' + ', '.join(sorted(extra)))
    if not isinstance(event.get('request_id'), str) or not isinstance(event.get('service'), str):
        raise Fail(2, 'trace requires request_id and service')
    expected = canonical(event)
    lookup = ('SELECT ' + ','.join('"' + key + '"' for key in TRACE_FIELDS) +
              ' FROM traces WHERE service=' + literal(event['service']) +
              ' AND request_id=' + literal(event['request_id']) + ' LIMIT 1')
    existing = query(cfg, lookup)
    if existing:
        if canonical(existing[0]) != expected:
            raise Fail(4, 'trace identifier already exists with different content; refusing overwrite')
        return False
    status, result = call(cfg, 'POST', records('traces'), event)
    if status >= 400:
        # Resolve races and uncertain retries by exact payload comparison through SQL.
        existing = query(cfg, lookup)
        if existing and canonical(existing[0]) == expected:
            return False
        raise Fail(1, f'trace ingestion rejected (HTTP {status}); input remains available for retry')
    return True


def ingest(cfg, path, follow=False, interval=1):
    path = Path(path).absolute()
    positions = {}
    # Keep each cursor's inode alive until the cursor is discarded. Otherwise a
    # fast rotation can reuse an unlinked inode for a new file of the same size,
    # and its old offset would silently skip new records.
    handles = {}
    count = duplicates = 0
    try:
        while True:
            for candidate in (Path(str(path) + '.1'), path):
                try:
                    opened = candidate.open('rb')
                except FileNotFoundError:
                    if candidate == path and not follow:
                        raise Fail(2, f'trace file not found: {path}')
                    continue
                info = os.fstat(opened.fileno())
                key = (info.st_dev, info.st_ino)
                if key in handles:
                    opened.close()
                    handle = handles[key]
                else:
                    handle = opened
                    handles[key] = handle
                offset = positions.get(key, 0)
                if info.st_size < offset:
                    offset = 0
                handle.seek(offset)
                while True:
                    line = handle.readline(262145)
                    if not line:
                        break
                    if len(line) > 262144:
                        raise Fail(2, 'trace JSONL record exceeds 256 KiB')
                    if not line.endswith(b'\n'):
                        if follow:
                            break  # Writer has not completed this record yet.
                        raise Fail(2, 'incomplete final JSONL record; keep the file and retry after its writer finishes')
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeError):
                        raise Fail(2, 'invalid JSONL trace; no records after this line were ingested') from None
                    inserted = ingest_one(cfg, event)
                    count += int(inserted)
                    duplicates += int(not inserted)
                    positions[key] = handle.tell()
            if not follow:
                return {'inserted': count, 'duplicates': duplicates}
            # At rest, retain only the two files the producer keeps. Closing a
            # removed inode and forgetting its cursor happen together.
            active = set()
            for candidate in (Path(str(path) + '.1'), path):
                try:
                    info = candidate.stat()
                    active.add((info.st_dev, info.st_ino))
                except FileNotFoundError:
                    pass
            for key in list(handles):
                if key not in active:
                    positions.pop(key, None)
                    handles.pop(key).close()
            time.sleep(interval)
    finally:
        for handle in handles.values():
            handle.close()


def run(args):
    if args.command == 'capture':
        import capture
        return capture.run(args)
    cfg = config()
    if args.command == 'logout':
        cache_file(cfg).unlink(missing_ok=True)
        return 0
    if args.command == 'login':
        google_login(cfg, args.port, args.timeout)
        say(f'Signed in as {cfg["email"]} at {cfg["url"]}', sys.stdout)
        return 0
    if args.command == 'check':
        return check(cfg)
    if args.command == 'dashboard':
        import dashboard
        return dashboard.serve(cfg, args.port)
    if args.command == 'whoami':
        response = must(cfg, 'POST', '/api/collections/users/auth-refresh')
        hide(response.get('token'))
        record = response['record']
        data = {'id': record['id'], 'name': record.get('name', ''), 'email': cfg['email'], 'url': cfg['url']}
    elif args.command == 'schema':
        data = must(cfg, 'GET', '/api/context/schema')
    elif args.command in ('sql', 'query'):
        sql = sys.stdin.read() if args.query == '-' else args.query
        data = must(cfg, 'POST', '/api/context/query', {'sql': sql})
        if data.get('truncated'):
            say('Result truncated; narrow the query or page with a stable ordering.')
    elif args.command == 'ingest':
        data = ingest(cfg, args.file, args.follow)
    elif args.command == 'trace':
        where = 'request_id=' + literal(args.request_id)
        if args.service:
            where += ' AND service=' + literal(args.service)
        traces = query(cfg, 'SELECT * FROM traces WHERE ' + where + ' ORDER BY service LIMIT 20')
        data = {'traces': traces}
    elif args.command == 'recent':
        data = query(cfg, 'SELECT id,service,request_id,correlation_id,method,route,started_at,duration_ms,status FROM traces ORDER BY started_at DESC,id DESC LIMIT ' + str(args.limit))
    elif args.command == 'report':
        data = query(cfg, "SELECT service,method,route,count(id) AS requests,round(avg(duration_ms),3) AS avg_ms,max(duration_ms) AS max_ms,sum(CASE WHEN status=0 OR status>=400 THEN 1 ELSE 0 END) AS errors FROM traces WHERE started_at >= strftime('%Y-%m-%d %H:%M:%fZ','now','-24 hours') GROUP BY service,method,route ORDER BY max_ms DESC LIMIT 100")
    say(dump(data, args.pretty), sys.stdout)
    return 0


def parse(argv):
    pretty = argparse.ArgumentParser(add_help=False)
    pretty.add_argument('--pretty', action='store_true', default=argparse.SUPPRESS)
    parser = argparse.ArgumentParser(prog='oc.py', parents=[pretty], description='ObserveContext: immutable request traces, SQL analysis and live request dashboard.')
    commands = parser.add_subparsers(dest='command', required=True)
    def add(name, help):
        return commands.add_parser(name, parents=[pretty], help=help)
    login_parser = add('login', 'Google browser login with a loopback callback')
    login_parser.add_argument('--google', action='store_true', required=True)
    login_parser.add_argument('--port', type=int, default=8765)
    login_parser.add_argument('--timeout', type=int, default=180)
    for name in ('whoami', 'logout', 'schema', 'check', 'report'):
        add(name, name)
    for name in ('sql', 'query'):
        add(name, 'read-only SQL; use - for stdin').add_argument('query')
    recent = add('recent', 'recent completed requests')
    recent.add_argument('--limit', type=int, choices=range(1, 501), metavar='1..500', default=50)
    trace = add('trace', 'inspect a request by request_id')
    trace.add_argument('request_id')
    trace.add_argument('--service')
    ingest_parser = add('ingest', 'upload JSONL traces through REST; identical retries are safe')
    ingest_parser.add_argument('file')
    ingest_parser.add_argument('--follow', action='store_true', help='poll the active file and its .1 rotation each second')
    dashboard_parser = add('dashboard', 'serve a private loopback dashboard; tokens stay in Python')
    dashboard_parser.add_argument('--port', type=int, default=8766)
    capture_parser = add('capture', 'run an existing Python skill script with HTTP timing')
    capture_parser.add_argument('--url', required=True, help='origin of the application to trace')
    capture_parser.add_argument('--service', required=True, help='distinct client service label, e.g. peoplecontext-client')
    capture_parser.add_argument('--output', required=True, help='private local JSONL output file')
    capture_parser.add_argument('--capture-sql', action='store_true')
    capture_parser.add_argument('script', help='Python script path, without a python executable prefix')
    capture_parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    args.pretty = getattr(args, 'pretty', False)
    return args


def main():
    try:
        return run(parse(sys.argv[1:]))
    except Fail as error:
        say(f'oc.py: {error}')
        return error.code
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        say(f'oc.py: {type(error).__name__}: {error}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
