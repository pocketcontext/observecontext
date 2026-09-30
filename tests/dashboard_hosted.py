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
    """Synthetic HTTP transport."""
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
            parsed=urllib.parse.urlsplit(self.path)
            if parsed.path == '/authorize':
                params=urllib.parse.parse_qs(parsed.query)
                code=secrets.token_urlsafe(24)
                redirect=params['redirect_uri'][0]
                codes[code]={'challenge':params['code_challenge'][0], 'redirect':redirect,
                             'user':{'sub':'alice@example.com','email':'alice@example.com','name':'Alice','email_verified':True,'hd':'example.com'}}
                self.send_response(302)
                self.send_header('Location',redirect+'?'+urllib.parse.urlencode({'code':code,'state':params['state'][0]}))
                self.end_headers()
                return
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
def hosted_server(binary):
    with tempfile.TemporaryDirectory(prefix='observe-hosted-test-') as tmp:
        root = Path(tmp)
        for name in ('pb_hooks', 'pb_migrations', 'web'):
            shutil.copytree(ROOT / name, root / name)
        shutil.copy2(ROOT / 'pocketcontext.json', root / 'pocketcontext.json')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        env = {**os.environ, 'BASE_URL': origin,
               'OBSERVECONTEXT_GOOGLE_WORKSPACE_DOMAIN': 'example.com', 'OBSERVECONTEXT_RATE_LIMITS': 'false'}
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
    parser.add_argument('--browser-module')
    args = parser.parse_args()
    with google_fixture() as (provider_url, codes), hosted_server(args.binary) as (origin, request):
        admin = request('POST', '/api/collections/_superusers/auth-with-password',
                        {'identity': 'admin@example.com', 'password': 'SyntheticAdminPassword123!'})['token']
        provider = {'name': 'google', 'clientId': 'synthetic-client', 'clientSecret': 'synthetic-secret',
                    'authURL': provider_url + '/authorize', 'tokenURL': provider_url + '/token',
                    'userInfoURL': provider_url + '/userinfo'}
        request('PATCH', '/api/collections/users', {'oauth2': {'enabled': True, 'providers': [provider]}}, admin)
        password='SyntheticHostedPassword123!'
        identities=[]
        marker="SELECT '<img src=x onerror=window.__traceExecuted=true><script>alert(1)</script>'"
        for index,email in enumerate(['alice@example.com','bob@example.com']):
            user=request('POST','/api/collections/users/records',{'name':email.split('@')[0],'email':email,'password':password,'passwordConfirm':password,'verified':True},admin)
            auth=request('POST','/api/collections/users/auth-with-password',{'identity':email,'password':password})
            identities.append(auth)
            operation=request('POST','/api/collections/operations/records',{'source':'hosted-synthetic'},auth['token'])
            payload=fixture(operation=operation['id'])
            payload.update(sql=marker,request_id=str(index+1)*32,duration_ms=20,
                spans=[{'name':'auth','offset_ms':0,'duration_ms':8},{'name':'sql.execute','offset_ms':4,'duration_ms':10},
                       {'name':'sql.scan','offset_ms':12,'duration_ms':3},{'name':'encode','offset_ms':20,'duration_ms':0}])
            request('POST','/api/collections/traces/records',payload,auth['token'])
        anonymous=Browser()
        html,headers=anonymous.request(origin+'/dashboard')
        assert b'type="module"' in html and "script-src 'self'" in headers['Content-Security-Policy']
        for asset in ['dashboard.js','api.js','pocketbase.es.mjs','dashboard.css']:
            anonymous.request(origin+'/dashboard/assets/'+asset)
        request('POST','/api/context/query',{'sql':'SELECT id FROM traces'},expected=(401,403))
        anonymous.request(origin+'/api/dashboard/data',expected=404)
        for identity in identities:
            result=request('POST','/api/context/query',{'sql':'SELECT created_by FROM traces'},identity['token'])
            assert result['rows']==[[identity['record']['id']]]
        if args.browser_module:
            payload={'url':origin+'/dashboard','marker':marker,'alice':identities[0],'bob':identities[1],
                     'password':password,'admin':admin}
            result=subprocess.run(['node',str(ROOT/'tests/dashboard_hosted_browser.cjs'),args.browser_module],
                                  input=json.dumps(payload),text=True,capture_output=True)
            assert result.returncode==0,result.stdout+result.stderr
            print(result.stdout.strip())
    print('PASS hosted SDK assets, CSP, direct authenticated SQL ownership and removed session proxy')

if __name__ == '__main__':
    main()
