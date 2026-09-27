#!/usr/bin/env python3
"""Synthetic hosted-dashboard authentication and private read acceptance tests."""
import argparse
import base64
import contextlib
import hashlib
import http.cookiejar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import tempfile
import time
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request

from integration import ROOT, fixture


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args):
        return None


class Browser:
    """Independent browser cookie jar; never exposes backend bearer tokens."""
    def __init__(self):
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies), NoRedirect())

    def request(self, url, *, method='GET', body=None, headers=None, expected=200):
        fields = dict(headers or {})
        if body is not None:
            fields.setdefault('Content-Type', 'application/json')
        req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                     headers=fields, method=method)
        try:
            response = self.opener.open(req, timeout=20)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status, raw, fields = response.status, response.read(), response.headers
        allowed = expected if isinstance(expected, tuple) else (expected,)
        assert status in allowed, (method, urllib.parse.urlsplit(url).path, status, allowed, raw[:300])
        return raw, fields

    def json(self, url, **kwargs):
        raw, headers = self.request(url, **kwargs)
        return json.loads(raw), headers


@contextlib.contextmanager
def google_fixture():
    codes, tokens = {}, {}
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, data):
            raw = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if self.path != '/token':
                return self.reply(404, {})
            fields = urllib.parse.parse_qs(self.rfile.read(int(self.headers.get('Content-Length', 0))).decode())
            get = lambda key: fields.get(key, [''])[0]
            pending = codes.pop(get('code'), None)
            digest = hashlib.sha256(get('code_verifier').encode()).digest()
            challenge = base64.urlsafe_b64encode(digest).decode().rstrip('=')
            if not pending or challenge != pending['challenge'] or get('redirect_uri') != pending['redirect']:
                return self.reply(400, {'error': 'invalid_grant'})
            if get('grant_type') != 'authorization_code':
                return self.reply(400, {'error': 'unsupported_grant_type'})
            token = secrets.token_urlsafe(24)
            tokens[token] = pending['user']
            return self.reply(200, {'access_token': token, 'token_type': 'Bearer', 'expires_in': 3600})

        def do_GET(self):
            user = tokens.get(self.headers.get('Authorization', '').removeprefix('Bearer '))
            if self.path != '/userinfo' or not user:
                return self.reply(401, {})
            return self.reply(200, user)

    http = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{http.server_port}', codes
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


@contextlib.contextmanager
def hosted_server(binary, ttl=3600):
    with tempfile.TemporaryDirectory(prefix='observe-hosted-test-') as tmp:
        root = Path(tmp)
        for name in ('pb_hooks', 'pb_migrations', 'web'):
            shutil.copytree(ROOT / name, root / name)
        shutil.copy2(ROOT / 'pocketcontext.json', root / 'pocketcontext.json')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        env = {**os.environ, 'BASE_URL': origin, 'OBSERVECONTEXT_DASHBOARD_INTERNAL_URL': origin,
               'OBSERVECONTEXT_GOOGLE_WORKSPACE_DOMAIN': 'example.com', 'OBSERVECONTEXT_RATE_LIMITS': 'false',
               'OBSERVECONTEXT_DASHBOARD_SESSION_TTL_SECONDS': str(ttl)}
        for key in ('OBSERVECONTEXT_GOOGLE_CLIENT_ID', 'OBSERVECONTEXT_GOOGLE_CLIENT_SECRET'):
            env.pop(key, None)
        common = [str(Path(binary).resolve()), '--dir', str(root / 'pb_data'),
                  '--migrationsDir', str(root / 'pb_migrations'), '--hooksDir', str(root / 'pb_hooks')]
        result = subprocess.run(common + ['superuser', 'upsert', 'admin@example.com', 'SyntheticAdminPassword123!'],
                                cwd=root, env=env, capture_output=True)
        assert result.returncode == 0, 'Isolated provisioning failed'
        with (root / 'server.log').open('w+') as log:
            proc = subprocess.Popen(common + ['serve', '--http', f'127.0.0.1:{port}'],
                                    cwd=root, env=env, stdout=log, stderr=log)
            browser = Browser()
            def request(method, path, body=None, token=None, expected=200):
                return browser.json(origin + path, method=method, body=body,
                                    headers={'Authorization': token} if token else {}, expected=expected)[0]
            try:
                for _ in range(150):
                    try:
                        request('GET', '/api/health')
                        break
                    except (OSError, AssertionError):
                        if proc.poll() is not None:
                            log.seek(0)
                            raise AssertionError(log.read())
                        time.sleep(.1)
                else:
                    raise AssertionError('Isolated hosted server startup timed out')
                yield origin, request
            finally:
                proc.terminate()
                proc.wait(timeout=15)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True)
    parser.add_argument('--browser-module', help='Optional absolute path to installed Playwright module')
    args = parser.parse_args()
    with google_fixture() as (provider_url, codes), hosted_server(args.binary) as (origin, request):
        admin = request('POST', '/api/collections/_superusers/auth-with-password',
                        {'identity': 'admin@example.com', 'password': 'SyntheticAdminPassword123!'})['token']
        provider = {'name': 'google', 'clientId': 'synthetic-client', 'clientSecret': 'synthetic-secret',
                    'authURL': provider_url + '/authorize', 'tokenURL': provider_url + '/token',
                    'userInfoURL': provider_url + '/userinfo'}
        request('PATCH', '/api/collections/users', {'oauth2': {'enabled': True, 'providers': [provider]}}, admin)
        api = origin + '/api/dashboard/'
        anonymous = Browser()
        html, headers = anonymous.request(origin + '/dashboard')
        assert b'ObserveContext' in html and b'synthetic-secret' not in html
        for endpoint in ('data', 'report', 'trace?id=aaaaaaaaaaaaaaa'):
            anonymous.request(api + endpoint, expected=401)
        anonymous.request(api + 'data', headers={'Host': 'attacker.example'}, expected=(400,403))
        anonymous.request(api + 'query', method='POST', body={'sql': 'SELECT 1'}, expected=(404,405))

        def begin(browser):
            _, fields = browser.request(api + 'login', expected=(302,303,307))
            destination = fields['Location']
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(destination).query)
            assert destination.startswith(provider_url + '/authorize?')
            assert params['code_challenge_method'] == ['S256']
            assert params.get('code_challenge') and not params.get('code_verifier')
            return params

        def finish(browser, params, email, *, expected=(302,303), wrong_pkce=False, state=None):
            code = secrets.token_urlsafe(24)
            redirect = params['redirect_uri'][0]
            assert redirect == origin + '/api/oauth2-redirect'
            codes[code] = {'challenge': 'wrong' if wrong_pkce else params['code_challenge'][0],
                           'redirect': redirect,
                           'user': {'sub': email, 'email': email, 'name': email.split('@')[0],
                                    'email_verified': True, 'hd': 'example.com'}}
            return browser.request(redirect + '?' + urllib.parse.urlencode({'code': code, 'state': state or params['state'][0]}),
                                   expected=expected)

        def login(email):
            browser = Browser()
            params = begin(browser)
            finish(browser, params, email)
            finish(browser, params, email, expected=(400,401,403))
            session, fields = browser.json(api + 'session')
            assert session['user']['email'] == email and session.get('csrf')
            cookies = [cookie for cookie in browser.cookies if cookie.name.endswith('dashboard_session')]
            assert len(cookies) == 1 and cookies[0].has_nonstandard_attr('HttpOnly')
            assert cookies[0].path == '/' and cookies[0].get_nonstandard_attr('SameSite') == 'Lax'
            assert len(cookies[0].value) >= 32 and cookies[0].value.count('.') != 2
            assert not any(key in session for key in ('token', 'access_token', 'refresh_token'))
            assert fields['Cache-Control'] == 'no-store'
            return browser, session

        pending = Browser()
        params = begin(pending)
        finish(Browser(), params, 'alice@example.com', expected=(400,401,403))
        finish(pending, params, 'alice@example.com', state='ocd_wrong-state', expected=(400,401,403))
        wrong = Browser()
        finish(wrong, begin(wrong), 'alice@example.com', wrong_pkce=True, expected=(400,401,403,502))
        wrong.request(api + 'data', expected=401)
        alice, alice_session = login('alice@example.com')
        bob, bob_session = login('bob@example.com')
        alice_id, bob_id = alice_session['user']['id'], bob_session['user']['id']
        # Provision a password only for synthetic ingestion; browser login remains OAuth.
        password = 'SyntheticHostedPassword123!'
        tokens = []
        traces = []
        marker = "SELECT '<img src=x onerror=window.__traceExecuted=true><script>alert(1)</script>'"
        for index, (user_id, email) in enumerate(((alice_id, 'alice@example.com'), (bob_id, 'bob@example.com'))):
            request('PATCH', '/api/collections/users/records/' + user_id,
                    {'password': password, 'passwordConfirm': password}, admin)
            token = request('POST', '/api/collections/users/auth-with-password', {'identity': email, 'password': password})['token']
            tokens.append(token)
            operation = request('POST', '/api/collections/operations/records', {'source': 'hosted-synthetic'}, token)
            payload = fixture(operation=operation['id'])
            payload.update(sql=marker, request_id=str(index + 1) * 32)
            traces.append(request('POST', '/api/collections/traces/records', payload, token))
        # Password provisioning revokes old sessions. Reauthenticate through Google.
        alice, alice_session = login('alice@example.com')
        bob, bob_session = login('bob@example.com')
        data, _ = alice.json(api + 'data')
        assert {row['id'] for row in data['recent']} == {traces[0]['id']}
        detail, _ = alice.json(api + 'trace?id=' + traces[0]['id'])
        assert detail['trace']['sql'] == marker and detail['spans']
        raw, fields = alice.request(api + 'trace?id=' + traces[1]['id'], expected=(200,404))
        assert traces[1]['id'].encode() not in raw and marker.encode() not in raw
        alice.request(api + 'trace?id=' + urllib.parse.quote("' OR 1=1 --"), expected=(400,404))
        alice.request(api + 'data?sql=SELECT%20*%20FROM%20users', expected=(200,400))
        alice.request(api + 'logout', method='POST', headers={'Origin': origin}, expected=403)
        alice.request(api + 'logout', method='POST', headers={'Origin': 'https://attacker.example', 'X-CSRF-Token': alice_session['csrf']}, expected=403)
        alice.request(api + 'data')
        alice.request(api + 'data', headers={'Origin': 'https://attacker.example'}, expected=403)

        if args.browser_module:
            cookie_data = [{'name': cookie.name, 'value': cookie.value, 'url': origin,
                            'httpOnly': cookie.has_nonstandard_attr('HttpOnly'), 'secure': cookie.secure,
                            'sameSite': 'Lax'} for cookie in alice.cookies]
            payload = {'url': origin + '/dashboard', 'cookies': cookie_data, 'marker': marker,
                       'forbidden': [admin, *tokens, 'synthetic-secret']}
            result = subprocess.run(['node', str(ROOT / 'tests/dashboard_hosted_browser.cjs'), args.browser_module],
                                    input=json.dumps(payload), text=True, capture_output=True)
            assert result.returncode == 0, result.stdout + result.stderr
            print(result.stdout.strip())

        request('PATCH', '/api/collections/users/records/' + bob_id, {'can_view_all_traces': True}, admin)
        bob.request(api + 'data', expected=401)
        bob, bob_session = login('bob@example.com')
        data, _ = bob.json(api + 'data')
        assert {row['id'] for row in data['recent']} == {trace['id'] for trace in traces}
        bob.json(api + 'trace?id=' + traces[0]['id'])
        request('PATCH', '/api/collections/users/records/' + bob_id, {'can_view_all_traces': False}, admin)
        bob.request(api + 'data', expected=401)
        bob, bob_session = login('bob@example.com')
        data, _ = bob.json(api + 'data')
        assert {row['id'] for row in data['recent']} == {traces[1]['id']}
        request('PATCH', '/api/collections/users/records/' + bob_id, {'disabled': True}, admin)
        bob.request(api + 'data', expected=401)
        alice.request(api + 'logout', method='POST', headers={'Origin': origin, 'X-CSRF-Token': alice_session['csrf']}, expected=(200,204))
        alice.request(api + 'data', expected=401)
        # Exercise server-enforced expiry, independent of browser cookie expiry.
        with hosted_server(args.binary, ttl=1) as (origin, request):
            api = origin + '/api/dashboard/'
            admin = request('POST', '/api/collections/_superusers/auth-with-password',
                            {'identity': 'admin@example.com', 'password': 'SyntheticAdminPassword123!'})['token']
            request('PATCH', '/api/collections/users', {'oauth2': {'enabled': True, 'providers': [provider]}}, admin)
            expiring, _ = login('expires@example.com')
            cookie = next(cookie for cookie in expiring.cookies if cookie.name.endswith('dashboard_session'))
            captured_cookie = cookie.name + '=' + cookie.value
            time.sleep(1.1)
            Browser().request(api + 'data', headers={'Cookie': captured_cookie}, expected=401)
            # Simulate the trusted TLS terminator: HTTPS origin and canonical Host,
            # while the fixture's actual transport remains private loopback HTTP.
            public_origin = 'https://observe.example.test'
            request('PATCH', '/api/settings', {'meta': {'appURL': public_origin}}, admin)
            secure_browser = Browser()
            _, fields = secure_browser.request(api + 'login', headers={'Host': 'observe.example.test'}, expected=302)
            cookie_header = fields['Set-Cookie']
            assert '__Host-oc_dashboard_flow=' in cookie_header
            assert 'Secure' in cookie_header and 'HttpOnly' in cookie_header and 'Path=/' in cookie_header
            assert 'Domain=' not in cookie_header
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(fields['Location']).query)
            code = secrets.token_urlsafe(24)
            codes[code] = {'challenge': params['code_challenge'][0], 'redirect': public_origin + '/api/oauth2-redirect',
                           'user': {'sub': 'secure@example.com', 'email': 'secure@example.com',
                                    'name': 'Secure', 'email_verified': True, 'hd': 'example.com'}}
            callback = origin + '/api/oauth2-redirect?' + urllib.parse.urlencode({'code': code, 'state': params['state'][0]})
            _, fields = secure_browser.request(callback, headers={'Host': 'observe.example.test',
                            'Cookie': cookie_header.split(';')[0]}, expected=303)
            session_header = next(value for value in fields.get_all('Set-Cookie') if '__Host-oc_dashboard_session=' in value)
            assert 'Secure' in session_header and 'HttpOnly' in session_header and 'Path=/' in session_header
            assert 'Domain=' not in session_header

    print('PASS hosted OAuth/PKCE, session ownership, fixed views, CSRF, read-all revocation and disabled accounts')


if __name__ == '__main__':
    main()
