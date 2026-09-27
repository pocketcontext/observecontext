#!/usr/bin/env python3
"""Checks of the ObserveContext container image. Python 3 standard library and the docker CLI only.

  python3 docker/smoke.py smoke   --image IMAGE   start as ONCE does, provision an user, exercise REST and SQL, stop, start again
  python3 docker/smoke.py config  --image IMAGE   startup errors for missing or unusable Litestream configuration
  python3 docker/smoke.py restore --image IMAGE   replicate to MinIO, destroy container and volume, restore into an empty volume

Every step prints a `==>` line before it runs and every docker command is printed. Secrets reach docker through the
environment (`-e NAME`), never through the command line, and are masked in everything this script prints. On failure
the script prints the masked logs of every container it started, then removes its containers, volumes, and network.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
# CI-only fixture built from pinned official MinIO source; no withdrawn registry dependency.
MINIO_IMAGE = 'observecontext-minio-fixture:9e49d5e-7394ce0'
STOP_LIMIT = 10  # seconds. `docker stop` waits 10 seconds by default before it kills.
JWT = re.compile(r'eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}')

hidden = []      # secret values: masked in output, searched for in container logs
containers = []  # every container started, for the failure report and cleanup
volumes = []
networks = []


class Failure(Exception):
    pass


def secret(value):
    """Register a secret value. Returns it."""
    if value and value not in hidden:
        hidden.append(value)
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            print(f'::add-mask::{value}', flush=True)
    return value


def mask(text):
    for value in hidden:
        text = text.replace(value, '***')
    return JWT.sub('***JWT***', text)


def say(text=''):
    print(mask(text), flush=True)


def step(text):
    say(f'==> {text}')


def check(condition, text):
    if not condition:
        raise Failure(text)
    say(f'    ok: {text}')


def docker(*args, env=None, ok=True, timeout=300):
    """Run docker. Returns (exit status, stdout + stderr). `env` adds variables to docker's own environment."""
    say('    $ docker ' + ' '.join(args))
    try:
        done = subprocess.run(['docker', *args], env={**os.environ, **(env or {})}, text=True, errors='replace',
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise Failure(f'docker {args[0]} did not finish within {timeout} seconds. Output so far:\n{error.stdout or ""}')
    if ok and done.returncode != 0:
        raise Failure(f'docker {args[0]} exited with status {done.returncode}:\n{done.stdout}')
    return done.returncode, done.stdout


def logs(name):
    return docker('logs', name, ok=False)[1]


def state(name):
    """Returns (running, exit status)."""
    status, text = docker('inspect', '-f', '{{.State.Running}} {{.State.ExitCode}} {{.State.OOMKilled}}', name, ok=False)
    if status != 0:
        raise Failure(f'container {name} cannot be inspected:\n{text}')
    running, code, oom = text.split()
    check(oom == 'false', f'{name} was not killed for memory')
    return running == 'true', int(code)


def run_app(image, name, volume, env, network=None):
    """Start the image detached with a named volume at /storage and port 80 published on a free local port."""
    docker('volume', 'create', volume)
    volumes.append(volume)
    args = ['run', '-d', '--name', name, '-p', '127.0.0.1::80', '-v', f'{volume}:/storage']
    if network:
        args += ['--network', network]
    for key in env:
        args += ['-e', key]
    containers.append(name)
    docker(*args, image, env=env)


def http(method, url, body=None, token=None, headers=None):
    """Returns (status, headers, parsed JSON or text). Status 0 means no HTTP response."""
    request = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode())
    if body is not None:
        request.add_header('Content-Type', 'application/json')
    if token:
        request.add_header('Authorization', token)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status, reply, raw = response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        status, reply, raw = error.code, error.headers, error.read()
    except (OSError, urllib.error.URLError) as error:
        return 0, {}, str(error)
    text = raw.decode(errors='replace')
    try:
        return status, reply, json.loads(text)
    except ValueError:
        return status, reply, text


def wait_up(name, limit=120):
    """Wait for GET /up. Fails early when the container exits. Returns the base URL."""
    step(f'waiting up to {limit} seconds for GET /up on {name}')
    deadline, last = time.time() + limit, ''
    while time.time() < deadline:
        running, code = state_quiet(name)
        if not running:
            raise Failure(f'{name} exited with status {code} before /up answered')
        base = base_url_quiet(name)
        if base:
            status, _, body = http('GET', base + '/up')
            last = f'status {status}, body {str(body)[:200]!r}'
            if status == 200:
                check(True, f'/up answered 200 without credentials at {base}')
                return base
        time.sleep(1)
    raise Failure(f'/up did not answer 200 within {limit} seconds; last answer: {last}')


def state_quiet(name):
    done = subprocess.run(['docker', 'inspect', '-f', '{{.State.Running}} {{.State.ExitCode}}', name], text=True, capture_output=True)
    if done.returncode != 0:
        raise Failure(f'container {name} cannot be inspected: {done.stderr}')
    running, code = done.stdout.split()
    return running == 'true', int(code)


def base_url_quiet(name):
    done = subprocess.run(['docker', 'port', name, '80/tcp'], text=True, capture_output=True)
    match = re.search(r'127\.0\.0\.1:(\d+)', done.stdout)
    return f'http://127.0.0.1:{match.group(1)}' if match else ''


def superuser_token(base, email, password):
    status, _, body = http('POST', base + '/api/collections/_superusers/auth-with-password', {'identity': email, 'password': password})
    if status != 200 or not isinstance(body, dict) or not body.get('token'):
        raise Failure(f'superuser login answered {status}: {str(body)[:500]}')
    check(True, 'superuser login with OBSERVECONTEXT_SUPERUSER_EMAIL and OBSERVECONTEXT_SUPERUSER_PASSWORD')
    return secret(body['token'])


def provision_user(base, token, email, password):
    status, _, body = http('POST', base + '/api/collections/users/records', token=token,
                           body={'name': 'Image check user', 'email': email, 'password': password, 'passwordConfirm': password})
    if status != 200 or not isinstance(body, dict) or not body.get('id'):
        raise Failure(f'creating the user with the superuser token answered {status}: {str(body)[:500]}')
    check(True, 'user created with the superuser token')
    return body['id']


class Client:
    """An ordinary default-users identity using REST writes and authenticated SQL reads."""

    def __init__(self, base, email, password, home):
        self.base = base
        status, _, body = http('POST', base + '/api/collections/users/auth-with-password',
                               {'identity': email, 'password': password})
        check(status == 200 and bool(body.get('token')), 'provisioned user can authenticate')
        self.token = secret(body['token'])
        self.user = body['record']

    def run(self, command, *args):
        if command == 'whoami':
            return json.dumps(self.user)
        if command == 'check':
            status, _, body = http('GET', self.base + '/api/context/schema', token=self.token)
        elif command == 'create':
            status, _, body = http('POST', self.base + '/api/collections/' + args[0] + '/records',
                                   json.loads(args[1]), token=self.token)
        else:
            raise Failure('unsupported smoke client operation')
        check(status == 200, f'{command} answered 200')
        return json.dumps(body)

    def sql(self, query):
        status, _, body = http('POST', self.base + '/api/context/query', {'sql': query}, token=self.token)
        check(status == 200, 'authenticated SQL query succeeded')
        return body['rows']


def write_record(client, user_id):
    operation = json.loads(client.run('create', 'operations', json.dumps({
        'source': 'container-smoke', 'client_key': secrets.token_hex(16),
    })))
    check(operation['owner'] == user_id, 'operation owner comes from authenticated user')
    payload = {
        'operation': operation['id'],
        'version': 1, 'request_id': secrets.token_hex(16), 'correlation_id': 'synthetic-container',
        'service': 'container-smoke', 'method': 'POST', 'route': '/api/context/query',
        'started_at': '2026-09-27T12:00:00.000Z', 'duration_ms': 12.5, 'status': 200,
        'user_id': user_id, 'sql': 'SELECT 1', 'rows': 1, 'truncated': False,
        'spans': [{'name': 'sql.execute', 'offset_ms': 1, 'duration_ms': 10}],
    }
    record = json.loads(client.run('create', 'traces', json.dumps(payload)))
    return record['id'], operation['id']


def check_records(client, trace, operation):
    rows = client.sql(f"SELECT service, sql, status, duration_ms FROM traces WHERE id = '{trace}'")
    check(rows == [['container-smoke', 'SELECT 1', 200, 12.5]], 'synthetic trace survives persistence and restore')
    owners = client.sql(f"SELECT o.id,o.owner FROM operations o JOIN traces t ON t.operation=o.id WHERE t.id='{trace}'")
    check(len(owners) == 1 and owners[0][1] == client.user['id'] and
          (operation is None or owners[0][0] == operation), 'owned operation relation survives persistence and restore')
    spans = client.sql(f"SELECT name, offset_ms, duration_ms FROM spans WHERE trace = '{trace}'")
    check(spans == [['sql.execute', 1, 10]], 'trace spans survive persistence and restore')


def check_logs(name, text=None):
    text = logs(name) if text is None else text
    found = [index for index, value in enumerate(hidden) if value in text]
    check(not found, f'the logs of {name} contain none of the {len(hidden)} secret values of this run (matches: {len(found)})')
    check(not JWT.search(text), f'the logs of {name} contain no token')
    return text


def stop(name):
    step(f'docker stop {name}: the server must exit by itself within {STOP_LIMIT} seconds with status 0')
    start = time.time()
    docker('stop', '-t', '30', name)
    elapsed = time.time() - start
    running, code = state(name)
    check(not running and elapsed < STOP_LIMIT, f'stopped in {elapsed:.1f} seconds')
    check(code == 0, f'exit status 0 (got {code}; 137 means it was killed, 143 that the signal was not handled)')


def once_env(extra=None):
    """The variables ONCE injects, with throwaway values, plus a superuser."""
    env = {
        'BASE_URL': 'https://observe.example.test', 'SECRET_KEY_BASE': secret(secrets.token_hex(32)), 'DISABLE_SSL': 'true', 'NUM_CPUS': '2',
        'SMTP_ADDRESS': 'smtp.example.test', 'SMTP_PORT': '587', 'SMTP_USERNAME': 'image-check', 'SMTP_PASSWORD': secret(secrets.token_urlsafe(24)),
        'MAILER_FROM_ADDRESS': 'Info <info@notifications.example.test>',
        'OBSERVECONTEXT_SUPERUSER_EMAIL': 'operator@example.test', 'OBSERVECONTEXT_SUPERUSER_PASSWORD': secret('-' + secrets.token_urlsafe(24)),
        'OBSERVECONTEXT_GOOGLE_CLIENT_ID': 'image-test.apps.googleusercontent.com',
        'OBSERVECONTEXT_GOOGLE_CLIENT_SECRET': secret(secrets.token_urlsafe(24)),
        'OBSERVECONTEXT_GOOGLE_WORKSPACE_DOMAIN': 'example.test',
    }
    env.update(extra or {})
    return env


def smoke(image, tmp, run_id):
    name, volume = f'oc-smoke-{run_id}', f'oc-smoke-{run_id}'
    env = once_env({'LITESTREAM_DISABLED': 'true'})
    user_email, user_password = 'user@example.test', secret(secrets.token_urlsafe(24))

    step('starting the image with the ONCE variables, LITESTREAM_DISABLED=true, a superuser, and a volume at /storage')
    run_app(image, name, volume, env)
    base = wait_up(name)
    check(docker('exec', name, 'cat', '/proc/1/comm')[1].strip() == 'tini', 'PID 1 is tini')

    step('hosted dashboard assets are packaged and private endpoints require a session')
    dashboard_headers = {'Host': 'observe.example.test'}
    status, reply, body = http('GET', base + '/', headers=dashboard_headers)
    check(status == 200 and 'Continue with Google' in body, 'hosted sign-in page is served')
    check("script-src 'self'" in reply.get('Content-Security-Policy', '') and
          'unsafe-inline' not in reply.get('Content-Security-Policy', ''), 'hosted assets use strict CSP')
    for asset in ['dashboard.js', 'dashboard.css']:
        status, _, body = http('GET', base + '/dashboard/assets/' + asset, headers=dashboard_headers)
        check(status == 200 and len(body) > 100, 'hosted ' + asset + ' is packaged')
    status, reply, _ = http('GET', base + '/api/dashboard/data', headers=dashboard_headers)
    check(status == 401, 'anonymous dashboard data is denied')

    step('settings taken from the environment, read with the superuser token')
    token = superuser_token(base, env['OBSERVECONTEXT_SUPERUSER_EMAIL'], env['OBSERVECONTEXT_SUPERUSER_PASSWORD'])
    status, _, settings = http('GET', base + '/api/settings', token=token)
    check(status == 200, f'GET /api/settings answered 200 (got {status})')
    check(settings['meta']['appURL'] == env['BASE_URL'], 'meta.appURL is BASE_URL')
    check(settings['smtp']['enabled'] is True and settings['smtp']['host'] == env['SMTP_ADDRESS'], 'SMTP is enabled with SMTP_ADDRESS as host')
    check(settings['rateLimits']['enabled'] is True, "rate limits are enabled by the image's default OBSERVECONTEXT_RATE_LIMITS=true")
    check(env['SMTP_PASSWORD'] not in json.dumps(settings), 'the settings API does not return the SMTP password')

    status, _, collection = http('GET', base + '/api/collections/users', token=token)
    check(status == 200 and collection['oauth2']['enabled'], 'Google OAuth is enabled after migrations')
    check(collection['oauth2']['providers'][0]['clientId'] == env['OBSERVECONTEXT_GOOGLE_CLIENT_ID'], 'Google client ID matches the environment')
    check(env['OBSERVECONTEXT_GOOGLE_CLIENT_SECRET'] not in json.dumps(collection), 'the collection API does not return the Google secret')
    check(collection['createRule'] == "@request.context = 'oauth2'" and collection['passwordAuth']['enabled'], 'OAuth-only signup rule and password login are preserved')

    status, _, _ = http('POST', base + '/api/collections/users/records',
                         {'email': 'public@example.test', 'name': 'Public signup',
                          'password': 'SyntheticPublicPassword123!', 'passwordConfirm': 'SyntheticPublicPassword123!'})
    check(status in (400, 403), 'public REST signup is denied despite the OAuth-only signup rule')

    step('CORS: only BASE_URL is an allowed origin')
    _, reply, _ = http('GET', base + '/api/health', headers={'Origin': env['BASE_URL']})
    check(reply.get('Access-Control-Allow-Origin') == env['BASE_URL'], 'BASE_URL is allowed')
    _, reply, _ = http('GET', base + '/api/health', headers={'Origin': 'https://other.example.test'})
    check(reply.get('Access-Control-Allow-Origin') is None, 'another origin is not allowed')

    step('provisioning an user and exercising authenticated endpoints against the container')
    user_id = provision_user(base, token, user_email, user_password)
    client = Client(base, user_email, user_password, tmp / 'home-smoke')
    check(json.loads(client.run('whoami'))['id'] == user_id, 'ordinary user login returns the provisioned id')
    client.run('check')
    check(True, "authenticated schema endpoint is available")
    trace, unused = write_record(client, user_id)
    check_records(client, trace, unused)

    check_logs(name)
    stop(name)

    step('starting the same container again: the volume keeps the data and the superuser upsert is repeatable')
    docker('start', name)
    base = wait_up(name)
    client = Client(base, user_email, user_password, tmp / 'home-smoke-2')
    check_records(client, trace, unused)
    text = check_logs(name)
    check('pbinstall' not in text, 'the logs contain no superuser installation link')
    stop(name)


def expect_startup_error(image, title, env, named, not_named=()):
    step(title)
    args = ['run', '--rm']
    for key in env:
        args += ['-e', key]
    status, text = docker(*args, image, env=env, ok=False, timeout=120)
    say('    output: ' + text.strip().replace('\n', '\n            '))
    check(status != 0, f'exit status is not 0 (got {status})')
    for variable in named:
        check(variable in text, f'the error names {variable}')
    for variable in not_named:
        check(variable not in text, f'the error does not name {variable}, which is set')
    check('Server started' not in text, 'the server did not start')
    check_logs('this run', text)


def config(image, tmp, run_id):
    required = ['LITESTREAM_BUCKET', 'LITESTREAM_PATH', 'LITESTREAM_ACCESS_KEY_ID', 'LITESTREAM_SECRET_ACCESS_KEY']
    expect_startup_error(image, 'no variables at all: replication is required unless it is switched off', {}, required)
    expect_startup_error(image, 'only the secret key is missing',
                         {'LITESTREAM_BUCKET': 'bucket', 'LITESTREAM_PATH': 'check/data', 'LITESTREAM_ACCESS_KEY_ID': 'key'},
                         ['LITESTREAM_SECRET_ACCESS_KEY'], ['LITESTREAM_BUCKET', 'LITESTREAM_PATH', 'LITESTREAM_ACCESS_KEY_ID'])
    expect_startup_error(image, 'LITESTREAM_DISABLED=1 is not exactly "true"', {'LITESTREAM_DISABLED': '1'}, required + ["exactly 'true'"])
    expect_startup_error(image, 'a superuser email without a password', {'LITESTREAM_DISABLED': 'true', 'OBSERVECONTEXT_SUPERUSER_EMAIL': 'operator@example.test'},
                         ['OBSERVECONTEXT_SUPERUSER_PASSWORD'])

    expect_startup_error(image, 'a Google client ID without a secret',
                         {'LITESTREAM_DISABLED': 'true', 'OBSERVECONTEXT_GOOGLE_CLIENT_ID': 'image-test.apps.googleusercontent.com'},
                         ['OBSERVECONTEXT_GOOGLE_CLIENT_SECRET'])
    expect_startup_error(image, 'a Google secret without a client ID',
                         {'LITESTREAM_DISABLED': 'true', 'OBSERVECONTEXT_GOOGLE_CLIENT_SECRET': secret(secrets.token_urlsafe(24))},
                         ['OBSERVECONTEXT_GOOGLE_CLIENT_ID'])

    step('a replica that cannot be reached: Litestream keeps retrying or fails, and the server never starts on an empty database')
    name = f'oc-config-{run_id}'
    env = {'LITESTREAM_BUCKET': 'bucket', 'LITESTREAM_PATH': 'check/data', 'LITESTREAM_REGION': 'us-east-1', 'LITESTREAM_ENDPOINT': 'http://127.0.0.1:9',
           'LITESTREAM_ACCESS_KEY_ID': 'key', 'LITESTREAM_SECRET_ACCESS_KEY': secret(secrets.token_hex(16))}
    run_app(image, name, f'oc-config-{run_id}', env)
    time.sleep(20)
    running, code = state(name)
    text = check_logs(name)
    say('    output: ' + text.strip()[-1500:].replace('\n', '\n            '))
    check('Server started' not in text and 'starting server' not in text, 'after 20 seconds the server has not started')
    check(running or code != 0, f'the container is still retrying or exited with an error (running: {running}, status: {code})')


def restore(image, tmp, run_id):
    network, minio, bucket = f'oc-drill-{run_id}', f'oc-drill-minio-{run_id}', 'observecontext-drill'
    minio_user, minio_password = 'drill' + secrets.token_hex(4), secret(secrets.token_hex(20))
    mc_env = {'MC_HOST_drill': secret(f'http://{minio_user}:{minio_password}@127.0.0.1:9000')}
    user_email, user_password = 'user@example.test', secret(secrets.token_urlsafe(24))

    step('building the CI-only MinIO fixture from pinned official source commits')
    docker('build', '--file', str(ROOT / 'docker/minio.Dockerfile'), '--tag', MINIO_IMAGE, str(ROOT / 'docker'), timeout=1200)
    step('starting MinIO as the S3 service on a private docker network')
    docker('network', 'create', network)
    networks.append(network)
    containers.append(minio)
    docker('run', '-d', '--name', minio, '--network', network, '-e', 'MINIO_ROOT_USER', '-e', 'MINIO_ROOT_PASSWORD', MINIO_IMAGE, 'server', '/data',
           env={'MINIO_ROOT_USER': minio_user, 'MINIO_ROOT_PASSWORD': minio_password})
    deadline = time.time() + 90
    while True:
        status, text = docker('exec', '-e', 'MC_HOST_drill', minio, 'mc', 'mb', '--ignore-existing', f'drill/{bucket}', env=mc_env, ok=False)
        if status == 0:
            break
        if time.time() > deadline:
            raise Failure(f'MinIO did not accept a bucket within 90 seconds:\n{text}')
        time.sleep(2)
    check(True, f'bucket {bucket} exists')

    def app_env(sync_interval):
        return once_env({'LITESTREAM_BUCKET': bucket, 'LITESTREAM_PATH': f'drill-{run_id}/data', 'LITESTREAM_REGION': 'us-east-1',
                         'LITESTREAM_ENDPOINT': f'http://{minio}:9000', 'LITESTREAM_ACCESS_KEY_ID': minio_user,
                         'LITESTREAM_SECRET_ACCESS_KEY': minio_password, 'LITESTREAM_SYNC_INTERVAL': sync_interval})

    def replica_files():
        text = docker('exec', '-e', 'MC_HOST_drill', minio, 'mc', 'ls', '--recursive', f'drill/{bucket}/drill-{run_id}/', env=mc_env, ok=False)[1]
        say('    ' + text.strip().replace('\n', '\n    '))
        return text.count('.ltx')

    def destroy(name, volume):
        docker('rm', '-f', '-v', name)
        docker('volume', 'rm', volume)
        volumes.remove(volume)
        status, _ = docker('volume', 'inspect', volume, ok=False)
        check(status != 0, f'container {name} and volume {volume} are gone')

    first, second, third = (f'oc-drill-{letter}-{run_id}' for letter in 'abc')

    step('container A: empty volume, empty replica, sync every second')
    env = app_env('1s')
    run_app(image, first, first, env, network)
    base = wait_up(first)
    token = superuser_token(base, env['OBSERVECONTEXT_SUPERUSER_EMAIL'], env['OBSERVECONTEXT_SUPERUSER_PASSWORD'])
    user_id = provision_user(base, token, user_email, user_password)
    client = Client(base, user_email, user_password, tmp / 'home-a')
    trace, unused = write_record(client, user_id)
    step('waiting 10 seconds for the sync, then listing the replica')
    time.sleep(10)
    check(replica_files() > 0, 'the replica holds LTX files')
    step('disaster: kill container A without a shutdown, remove it and its volume')
    docker('kill', first)
    text = check_logs(first)
    check('the replica holds no backup' in text, 'container A started from an empty replica')
    destroy(first, first)

    step('container B: empty volume, same replica, sync once an hour so that only the shutdown sync can save a late write')
    env = app_env('1h')
    run_app(image, second, second, env, network)
    base = wait_up(second)
    client = Client(base, user_email, user_password, tmp / 'home-b')
    check(json.loads(client.run('whoami'))['id'] == user_id, 'the user logs in with its password after the restore')
    check_records(client, trace, unused)
    client.run('check')
    text = logs(second)
    check('post-restore integrity check passed' in text, "Litestream's integrity check of the restored database passed")
    superuser_token(base, env['OBSERVECONTEXT_SUPERUSER_EMAIL'], env['OBSERVECONTEXT_SUPERUSER_PASSWORD'])

    step('a write immediately before docker stop must reach the replica through the shutdown sync')
    late, _ = write_record(client, user_id)
    stop(second)
    text = check_logs(second)
    check('waiting for initial database and replica synchronization' in text, 'replica readiness is required before serving')
    check('litestream shut down' in text, 'Litestream received the signal and shut down after the server')
    say('    container B replication and shutdown logs:\n' + text)
    destroy(second, second)

    step('container C: empty volume again; the late write must be there')
    run_app(image, third, third, app_env('1s'), network)
    base = wait_up(third)
    client = Client(base, user_email, user_password, tmp / 'home-c')
    check_records(client, trace, unused)
    check_records(client, late, None)
    check_logs(third)
    stop(third)
    check_logs(minio)


def report_and_clean(failed):
    if failed:
        for name in containers:
            if os.environ.get('GITHUB_ACTIONS') == 'true':
                say(f'::group::logs of {name}')
            text = logs(name)
            say(f'---- logs of {name} (masked) ----')
            say(text)
            say(docker('inspect', '-f', 'state: {{json .State}}', name, ok=False)[1])
            if os.environ.get('GITHUB_ACTIONS') == 'true':
                say('::endgroup::')
        say(docker('ps', '-a', ok=False)[1])
    step('cleaning up')
    for name in containers:
        docker('rm', '-f', '-v', name, ok=False)
    for name in volumes:
        docker('volume', 'rm', '-f', name, ok=False)
    for name in networks:
        docker('network', 'rm', name, ok=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('mode', choices=['smoke', 'config', 'restore'])
    parser.add_argument('--image', required=True, help='image reference that `docker run` can resolve locally')
    args = parser.parse_args()
    failed = True
    with tempfile.TemporaryDirectory(prefix='observecontext-image-') as tmp:
        try:
            {'smoke': smoke, 'config': config, 'restore': restore}[args.mode](args.image, Path(tmp), secrets.token_hex(3))
            failed = False
        except Failure as error:
            say(f'\nFAILED: {error}')
        except Exception as error:
            say(f'\nFAILED: unexpected {type(error).__name__}: {error}')
        finally:
            report_and_clean(failed)
    say('FAILED' if failed else f'PASSED: {args.mode}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
