"""Explicit, invocation-scoped instrumentation for packaged Context clients."""
from contextlib import contextmanager
import json
import os
import sys
from types import SimpleNamespace
from .capture import capture_session, capture_origins

CONFIG_ENV = "OBSERVECONTEXT_CAPTURE_V1"
_active = False


def configuration(raw):
    if len(raw) > 32768:
        raise ValueError("capture configuration too large")
    data = json.loads(raw)
    fields = {"version", "url", "origin", "service", "output", "upload", "spool", "flush_timeout", "capture_sql", "status_file"}
    if not isinstance(data, dict) or set(data) != fields or data["version"] != 1:
        raise ValueError("unsupported capture configuration")
    for field in ("upload", "capture_sql"):
        if not isinstance(data[field], bool):
            raise ValueError("invalid capture flag")
    for field in ("url", "output", "spool", "status_file"):
        if data[field] is not None and not isinstance(data[field], str):
            raise ValueError("invalid capture path or origin")
    if not isinstance(data["service"], str) or not isinstance(data["origin"], list) or not all(isinstance(x, str) for x in data["origin"]):
        raise ValueError("invalid capture origins")
    if type(data["flush_timeout"]) is not int or not 1 <= data["flush_timeout"] <= 120:
        raise ValueError("invalid flush timeout")
    args = SimpleNamespace(**data)
    capture_origins(args)
    if not args.upload and not args.output:
        raise ValueError("capture requires output or upload")
    return args


@contextmanager
def instrument_cli(*, service, opener=None):
    """Instrument this invocation only; nested activation is a no-op.

    The opener argument documents the client's transport. The urllib director
    is instrumented to include additional upload/download openers. Requests are
    selected by exact configured origin and a curated route allowlist.
    """
    global _active
    raw = os.environ.get(CONFIG_ENV)
    if not raw or _active or service.startswith("observecontext."):
        yield
        return
    try:
        args = configuration(raw)
    except Exception:
        print("ObserveContext: invalid capture configuration; command runs without tracing.", file=sys.stderr)
        yield
        return
    _active = True
    session = capture_session(args)
    try:
        try:
            session.__enter__()
        except Exception:
            print("ObserveContext: instrumentation unavailable; command runs without tracing.", file=sys.stderr)
            yield
            return
        if args.status_file:
            try:
                from .capture import append_trace
                append_trace(args.status_file, {"version": 1, "active": True})
            except Exception:
                print("ObserveContext: could not report instrumentation activation.", file=sys.stderr)
        try:
            yield
        finally:
            try:
                session.__exit__(None, None, None)
            except Exception:
                print("ObserveContext: telemetry finalization failed; command result preserved.", file=sys.stderr)
    finally:
        _active = False
