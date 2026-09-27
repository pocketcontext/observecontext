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
    output = Path(args.output).absolute() if getattr(args, 'output', None) else None
    upload = getattr(args, 'upload', False)
    delivery = None
    operation_key = uuid.uuid4().hex
    if upload:
        try:
            import oc
            from uploader import Delivery
            delivery = Delivery(oc, oc.config(), getattr(args, 'spool', None), timeout=2)
        except Exception:
            print('ObserveContext: telemetry identity unavailable; sign in before capture. The command will still run.', file=sys.stderr)
    elif output is None:
        raise ValueError('--output is required without --upload')
    original = urllib.request.OpenerDirector.open
    pending = set()
    source_tokens = set()
    original_redirect = urllib.request.HTTPRedirectHandler.redirect_request

    def redirect(handler, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(req.full_url)
        if (parsed.scheme, parsed.netloc) == (target.scheme, target.netloc) and req.get_header('Authorization'):
            return None  # Never forward a traced source credential through a redirect.
        return original_redirect(handler, req, fp, code, msg, headers, newurl)

    def retrieve(request_id, token):
        if not re.fullmatch('[0-9a-f]{32}', request_id or '') or not token:
            return None
        deadline = time.monotonic() + 1.5
        no_redirect = urllib.request.build_opener(type('TraceNoRedirect', (urllib.request.HTTPRedirectHandler,),
                                                     {'redirect_request': lambda *unused: None}))
        request = urllib.request.Request(args.url.rstrip('/') + '/api/context/traces/' + request_id)
        request.add_unredirected_header('Authorization', token)
        while True:
            try:
                with original(no_redirect, request, timeout=max(.01, deadline - time.monotonic())) as response:
                    raw = response.read(262145)
                if len(raw) > 262144:
                    raise ValueError('server trace too large')
                event = json.loads(raw)
                if not isinstance(event, dict) or event.get('version') != 1 or event.get('request_id') != request_id:
                    raise ValueError('unexpected server trace')
                # Never persist producer extensions, response bodies or credentials.
                import oc
                return {key: event[key] for key in oc.TRACE_FIELDS if key != 'operation' and key in event}
            except urllib.error.HTTPError as error:
                error.close()
                if error.code == 404 and time.monotonic() + .05 < deadline:
                    time.sleep(.05)
                    continue
                raise

    failed = False

    def traced(opener, fullurl, data=None, timeout=None):
        nonlocal failed
        request = fullurl if isinstance(fullurl, urllib.request.Request) else urllib.request.Request(fullurl, data=data)
        parsed = urllib.parse.urlsplit(request.full_url)
        same_origin = (parsed.scheme, parsed.netloc) == (target.scheme, target.netloc)
        authorization = request.get_header('Authorization')
        if upload and not same_origin and authorization and authorization in source_tokens:
            raise urllib.error.URLError('refusing to forward a source credential to another origin')
        route = route_for(parsed.path) if same_origin else None
        if not route:
            if timeout is None:
                return original(opener, fullurl, data)
            return original(opener, fullurl, data, timeout)
        source_token = request.get_header('Authorization')
        if upload and source_token:
            source_tokens.add(source_token)
            request.remove_header('X-context-trace')
            request.add_unredirected_header('X-Context-Trace', '1')
            request.remove_header('X-context-capture-sql')
            if args.capture_sql:
                request.add_unredirected_header('X-Context-Capture-Sql', '1')
        correlation = uuid.uuid4().hex
        request.remove_header('X-context-correlation-id')
        request.add_unredirected_header('X-Context-Correlation-Id', correlation)
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
        server_request_id = None

        def finish():
            nonlocal done, failed
            if done:
                return
            done = True
            pending.discard(finish)
            duration = (time.perf_counter() - start) * 1000
            event.update(duration_ms=duration, spans=[dict(name='http.client', offset_ms=0, duration_ms=duration)])
            if output:
                try:
                    append_trace(output, event)
                except (OSError, ValueError):
                    failed = True
                    print('ObserveContext: could not write a client trace.', file=sys.stderr)
            events = [event]
            if upload and source_token and server_request_id:
                try:
                    server_event = retrieve(server_request_id, source_token)
                    if server_event:
                        events.append(server_event)
                except Exception:
                    print('ObserveContext: server trace unavailable; keeping the client measurement.', file=sys.stderr)
            if delivery:
                try:
                    delivery.enqueue(operation_key, args.service, events)
                except Exception:
                    print('ObserveContext: could not persist telemetry; check private queue permissions or capacity.', file=sys.stderr)
        pending.add(finish)
        try:
            result = original(opener, request, data) if timeout is None else original(opener, request, data, timeout)
            event['status'] = result.status
            server_request_id = result.headers.get('X-Context-Request-Id')
            return Response(result, finish)
        except urllib.error.HTTPError as error:
            event['status'] = error.code
            server_request_id = error.headers.get('X-Context-Request-Id')
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
    if upload:
        urllib.request.HTTPRedirectHandler.redirect_request = redirect
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
        urllib.request.HTTPRedirectHandler.redirect_request = original_redirect
        sys.argv, sys.path[:] = old_argv, old_path
        for finish in list(pending):
            finish()
        if delivery:
            try:
                delivered = delivery.flush(timeout=getattr(args, 'flush_timeout', 10))
                print(f'ObserveContext: delivered {delivered} queued request pair(s).', file=sys.stderr)
            except Exception:
                print('ObserveContext: telemetry remains in the private queue; retry with oc.py flush.', file=sys.stderr)
    # Telemetry must never turn a successful source command into a failure.
    return exit_code if upload else exit_code or (1 if failed else 0)
