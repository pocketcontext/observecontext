"""Loopback-only dashboard; credentials stay in the Python process."""
import http.server
import json
from pathlib import Path
import re
import secrets
import urllib.parse


def make_handler(cfg, session, api=None):
    if api is None:
        import oc as api
    prefix = '/' + session + '/'
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            expected = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != expected or self.headers.get('Origin') not in (None, 'http://' + expected):
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
                    rows = api.query(cfg, 'SELECT id,service,request_id,correlation_id,method,route,started_at,duration_ms,status FROM traces ORDER BY started_at DESC,id DESC LIMIT 50')
                    report = api.query(cfg, "SELECT service,method,route,count(id) AS requests,round(avg(duration_ms),3) AS avg_ms,max(duration_ms) AS max_ms,sum(CASE WHEN status=0 OR status>=400 THEN 1 ELSE 0 END) AS errors FROM traces WHERE started_at >= strftime('%Y-%m-%d %H:%M:%fZ','now','-24 hours') GROUP BY service,method,route ORDER BY max_ms DESC LIMIT 100")
                    body, mime = json.dumps({'recent': rows, 'report': report}).encode(), 'application/json'
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


def serve(cfg, port):
    import oc
    if not 1 <= port <= 65535:
        raise ValueError('dashboard port must be 1..65535')
    oc.must(cfg, 'GET', '/api/context/schema')
    session = secrets.token_urlsafe(32)
    with http.server.HTTPServer(('127.0.0.1', port), make_handler(cfg, session, oc)) as server:
        print(f'ObserveContext dashboard: http://127.0.0.1:{port}/{session}/', flush=True)
        print(f'Over SSH forward port {port}. Press Ctrl-C to stop.', flush=True)
        server.serve_forever()
    return 0
