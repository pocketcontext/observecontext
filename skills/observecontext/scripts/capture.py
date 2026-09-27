"""Trace one existing standard-library Python skill client in this process."""
import datetime
import json
import os
from pathlib import Path
import re
import runpy
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def append_trace(path, event):
    """Append one private JSONL record; refuse symlinks and non-regular files."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('trace output must be an owned regular file')
        os.fchmod(fd, 0o600)
        data = (json.dumps(event, separators=(',', ':'), allow_nan=False) + '\n').encode()
        # Serialize writers, including separate capture processes using this file.
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
        while data:
            data = data[os.write(fd, data):]
    finally:
        os.close(fd)


def route_for(path):
    if path in ('/api/context/query', '/api/context/schema', '/api/batch'):
        return path
    match = re.fullmatch(r'/api/collections/([a-zA-Z0-9_]+)/records(?:/[^/]+)?', path)
    if match and match[1] not in ('users', 'agents', '_superusers'):
        suffix = '/{id}' if path.count('/') == 5 else ''
        return '/api/collections/' + match[1] + '/records' + suffix
    return None


class Response:
    def __init__(self, response, finish):
        self.response, self.finish = response, finish

    def __getattr__(self, name):
        return getattr(self.response, name)

    def read(self, amount=-1):
        try:
            data = self.response.read(amount)
            if amount is None or amount < 0 or not data:
                self.finish()
            return data
        except BaseException:
            self.finish()
            raise

    def close(self):
        try:
            return self.response.close()
        finally:
            self.finish()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def run(args):
    target = urllib.parse.urlsplit(args.url)
    if (target.scheme not in ('http', 'https') or not target.hostname or target.username
            or target.password or target.query or target.fragment or target.path not in ('', '/')):
        raise ValueError('--url must be an HTTP(S) origin without credentials or a path')
    if not re.fullmatch(r'[a-zA-Z0-9._-]{1,100}', args.service):
        raise ValueError('--service must be 1–100 letters, digits, dots, underscores or hyphens')
    script = Path(args.script).resolve(strict=True)
    output = Path(args.output).absolute()
    original = urllib.request.OpenerDirector.open
    pending = []
    failed = False

    def traced(opener, fullurl, data=None, timeout=None):
        nonlocal failed
        request = fullurl if isinstance(fullurl, urllib.request.Request) else urllib.request.Request(fullurl, data=data)
        parsed = urllib.parse.urlsplit(request.full_url)
        route = route_for(parsed.path) if (parsed.scheme, parsed.netloc) == (target.scheme, target.netloc) else None
        if not route:
            if timeout is None:
                return original(opener, fullurl, data)
            return original(opener, fullurl, data, timeout)
        correlation = uuid.uuid4().hex
        request.add_header('X-Context-Correlation-Id', correlation)
        event = dict(version=1, request_id=uuid.uuid4().hex, correlation_id=correlation,
                     service=args.service, method=request.get_method(), route=route,
                     started_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                     user_id='', rows=0, truncated=False, status=0, sql='')
        if args.capture_sql and route == '/api/context/query':
            try:
                payload = json.loads(data if data is not None else request.data)
                if isinstance(payload.get('sql'), str):
                    event['sql'] = payload['sql'].encode()[:16384].decode('utf-8', 'ignore')
            except (ValueError, TypeError, AttributeError):
                pass
        start = time.perf_counter()
        done = False

        def finish():
            nonlocal done, failed
            if done:
                return
            done = True
            duration = (time.perf_counter() - start) * 1000
            event.update(duration_ms=duration, spans=[dict(name='http.client', offset_ms=0, duration_ms=duration)])
            try:
                append_trace(output, event)
            except (OSError, ValueError):
                failed = True
                print('ObserveContext: could not write a client trace.', file=sys.stderr)
        pending.append(finish)
        try:
            result = original(opener, request, data) if timeout is None else original(opener, request, data, timeout)
            event['status'] = result.status
            return Response(result, finish)
        except urllib.error.HTTPError as error:
            event['status'] = error.code
            # Preserve HTTPError identity, while including its body consumption.
            read, close = error.read, error.close
            def error_read(amount=-1):
                try:
                    data = read(amount)
                    if amount is None or amount < 0 or not data:
                        finish()
                    return data
                except BaseException:
                    finish()
                    raise
            def error_close():
                try:
                    return close()
                finally:
                    finish()
            error.read, error.close = error_read, error_close
            raise
        except BaseException:
            finish()
            raise

    old_argv, old_path = sys.argv, list(sys.path)
    urllib.request.OpenerDirector.open = traced
    exit_code = 0
    try:
        sys.argv = [str(script), *args.arguments]
        sys.path.insert(0, str(script.parent))
        try:
            runpy.run_path(str(script), run_name='__main__')
        except SystemExit as error:
            exit_code = error.code or 0
    finally:
        urllib.request.OpenerDirector.open = original
        sys.argv, sys.path[:] = old_argv, old_path
        for finish in pending:
            finish()
    return exit_code or (1 if failed else 0)
