"""Loopback-bound dashboard; credentials stay in the Python process."""
import http.server
import json
from pathlib import Path
import re
import secrets
import urllib.parse



# Operation identity is an authorized server relation, never a producer's
# correlation label. Aggregate spans once: filtered snapshots have no indexes.
RECENT_SQL = """WITH recent_operations AS (
 SELECT operation,max(started_at) AS latest,max(id) AS tie
 FROM traces GROUP BY operation ORDER BY latest DESC,tie DESC LIMIT 50
), selected AS (
 SELECT id,operation,service,request_id,correlation_id,method,route,started_at,duration_ms,status
 FROM traces WHERE operation IN (SELECT operation FROM recent_operations)
), kinds AS (
 SELECT s.trace,max(CASE WHEN s.name='http.client' THEN 1 ELSE 0 END) AS client,
 max(CASE WHEN s.name='auth' THEN 1 ELSE 0 END) AS server
 FROM spans s WHERE s.trace IN (SELECT id FROM selected) GROUP BY s.trace
)
SELECT t.id,t.operation,t.service,t.request_id,t.correlation_id,t.method,t.route,
 t.started_at,t.duration_ms,t.status,
 CASE WHEN k.client=1 THEN 'client' WHEN k.server=1 THEN 'server' ELSE 'unknown' END AS kind,
 t.operation AS operation_key
 FROM selected t LEFT JOIN kinds k ON k.trace=t.id
 ORDER BY t.started_at DESC,t.id DESC LIMIT 500"""


def public_origin(value):
    """Accept one exact HTTPS origin, never a path or arbitrary Host override."""
    if value is None:
        return None
    parsed = urllib.parse.urlsplit(value)
    if (value != 'https://' + (parsed.hostname or '') or parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment
            or parsed.netloc != parsed.hostname or
            not re.fullmatch(r'[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?', parsed.hostname)):
        raise ValueError('public origin must be https:// followed by a lowercase DNS hostname, without port or path')
    return value


def make_handler(cfg, session, api=None, origin=None):
    origin = public_origin(origin)
    if api is None:
        from . import cli as api
    prefix = '/' + session + '/'
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            expected = f'127.0.0.1:{self.server.server_port}'
            origins = {'http://' + expected: expected}
            if origin:
                origins[origin] = urllib.parse.urlsplit(origin).netloc
            host = self.headers.get('Host')
            request_origin = self.headers.get('Origin')
            # Reject duplicates and mixed public/local origins. Forwarded headers
            # are never accepted as proof of origin or authentication.
            allowed = [key for key, value in origins.items() if value == host]
            if (len(self.headers.get_all('Host', [])) != 1 or
                    len(self.headers.get_all('Origin', [])) > 1 or not allowed or
                    request_origin not in (None, allowed[0])):
                self.send_error(403)
                return
            parsed = urllib.parse.urlsplit(self.path)
            if not parsed.path.startswith(prefix):
                self.send_error(404)
                return
            endpoint = parsed.path[len(prefix):]
            try:
                if endpoint == '':
                    body = Path(__file__).with_name('dashboard.html').read_bytes()
                    mime = 'text/html; charset=utf-8'
                elif endpoint == 'data':
                    rows = api.query(cfg, RECENT_SQL)
                    report = api.query(cfg, "SELECT service,method,route,count(id) AS requests,round(avg(duration_ms),3) AS avg_ms,max(duration_ms) AS max_ms,sum(CASE WHEN status=0 OR status>=400 THEN 1 ELSE 0 END) AS errors FROM traces WHERE started_at >= strftime('%Y-%m-%d %H:%M:%fZ','now','-24 hours') GROUP BY service,method,route ORDER BY max_ms DESC LIMIT 100")
                    body, mime = json.dumps({'recent': rows, 'recent_limited': len(rows) >= 500, 'report': report}).encode(), 'application/json'
                elif endpoint == 'trace':
                    record = urllib.parse.parse_qs(parsed.query).get('id', [''])[0]
                    if not re.fullmatch(r'[a-z0-9]{15}', record):
                        self.send_error(400)
                        return
                    traces = api.query(cfg, "SELECT * FROM traces WHERE id='" + record + "' LIMIT 1")
                    spans = api.query(cfg, "SELECT name,offset_ms,duration_ms FROM spans WHERE trace='" + record + "' ORDER BY offset_ms,ordinal LIMIT 128")
                    body, mime = json.dumps({'trace': traces[0] if traces else None, 'spans': spans}).encode(), 'application/json'
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', mime)
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                # Don't leak credentials or upstream payloads into the browser/log.
                self.send_error(502, 'Cannot read ObserveContext; check login and server availability')
    return Handler


def serve(cfg, port, origin=None):
    from . import cli as oc
    if not 1 <= port <= 65535:
        raise ValueError('dashboard port must be 1..65535')
    origin = public_origin(origin)
    oc.must(cfg, 'GET', '/api/context/schema')
    session = secrets.token_urlsafe(32)
    with http.server.HTTPServer(('127.0.0.1', port), make_handler(cfg, session, oc, origin)) as server:
        print(f'ObserveContext dashboard: http://127.0.0.1:{port}/{session}/', flush=True)
        if origin:
            print(f'Protected proxy dashboard: {origin}/{session}/', flush=True)
            print('Public origin enabled: the proxy must enforce authentication on every request.', flush=True)
        print(f'Over SSH forward port {port}. Press Ctrl-C to stop.', flush=True)
        server.serve_forever()
    return 0
