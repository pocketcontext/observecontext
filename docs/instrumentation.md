# Instrumentation and collection

The complete path is: an existing Python skill sends an HTTP request with a correlation ID; the instrumented PocketContext server emits its completed trace to a private JSONL spool; `oc.py ingest` imports that spool through ordinary authenticated REST; SQL and the dashboard read ObserveContext. Neither the app nor the server depends on an LLM telemetry service.

## Server

Build the server revision in ObserveContext's `POCKETCONTEXT_VERSION`. Adopt it intentionally in each source application's own pin after running that application's compatibility checks. Add this top-level member to the source app's existing `pocketcontext.json` and restart:

```json
"tracing": {
  "enabled": true,
  "service": "peoplecontext",
  "path": "./pb_data/traces.jsonl",
  "captureSql": false,
  "maxBytes": 16777216
}
```

The parent directory must already exist and be private. Keep one spool per server process. Authenticated ordinary-user API requests are captured; health requests, guests and superusers are omitted. HTTP responses include `X-Context-Request-Id`. Set `captureSql` only if all ObserveContext Workspace users may read SQL literals. No row results or REST bodies are recorded.

Server spans are `auth`, `sql.prepare` (including connection-pool waiting), `sql.execute`, `sql.scan` (including SQLite stepping and result accumulation), `response.encode`, and for filtered mode `snapshot.wait`, `snapshot.build`, `snapshot.reader_init`. These are elapsed durations, not CPU profiles. `sql.execute` alone is not the full SQLite query cost; include scan. REST currently reports total duration. No cache or database engine is changed by tracing.

The writer keeps up to 256 queued traces and two files: `traces.jsonl` and `traces.jsonl.1`, each bounded by `maxBytes`. It reports dropped counts without trace payloads. It is diagnostic telemetry, not durable audit evidence. A slow collector can lose old rotations; a crash can lose queued records. Keep ObserveContext's own tracing disabled to avoid collection recursively generating more traces.

## Existing skill clients

Wrap a standard-library Python client without editing it. Supply its usual app credentials/login cache as before. The wrapper needs no ObserveContext credentials until ingestion:

```sh
python3 /path/to/observecontext/scripts/oc.py capture \
  --url https://people.example.com \
  --service peoplecontext-client \
  --output /private/client.jsonl \
  /path/to/peoplecontext/scripts/pc.py query 'SELECT id FROM employees LIMIT 5'
```

Use the actual installed client's command names. `capture` runs a Python script in the same process and wraps `urllib.request`; do not put `python3` before the script argument. Requests to other origins, authentication endpoints, file downloads and non-API traffic are excluded. Existing Google login should be completed before wrapping an operation. Add `--capture-sql` only when sharing that SQL is intended. Authenticated source identity is recorded by the server, not inferred from client tokens.

Client and server traces have separate request IDs and service labels. Join on `correlation_id` to compare them. The client injects a fresh `X-Context-Correlation-Id` per captured request; the server validates it. Client spans measure through full response reading/closing, including transport and client consumption. Status zero means a transport failure before an HTTP response. It cannot attribute that failure to a specific server phase. The wrapper does not capture networking in child processes or non-urllib libraries. Client output is append-only, private JSONL; rotate/archive it between runs as needed.

## Collector

Run on the host that can read the private spool, with an ordinary ObserveContext account:

```sh
export OBSERVECONTEXT_URL=https://observe.example.com
export OBSERVECONTEXT_USER_EMAIL=you@example.com
python3 /path/to/observecontext/scripts/oc.py login --google
python3 /path/to/observecontext/scripts/oc.py ingest /private/traces.jsonl --follow
```

The collector checks both `.1` and the active file once per second. It keeps in-memory offsets; on restart it replays available records and compares existing content through SQL. Identical `(service,request_id)` submissions are skipped. Different content for an existing key fails instead of overwriting. This is an at-least-once retry strategy over the available spool, not a guarantee that every request was retained. A transport error stops the collector with the source file untouched: restart it after restoring connectivity. A malformed record also stops collection so later records are not silently skipped. Source times are normalized to UTC milliseconds by PocketBase; elapsed durations retain fractional milliseconds.

New traces appear in the dashboard after upload; the page polls every five seconds. No in-flight span stream is provided. See the skill's [SQL examples](../skills/observecontext/references/examples.md) for correlation and phase analysis.
