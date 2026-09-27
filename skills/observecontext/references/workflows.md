# Workflows

Replace `/skill` with the installed skill directory.

## Inspect a request

Run `python3 /skill/scripts/oc.py recent --pretty`, then `trace REQUEST_ID --service SERVICE`. Correlate client/server by `correlation_id` and inspect spans through SQL. If no server trace exists, confirm the source adopted the instrumented server pin and enabled its tracing configuration. Normal application activity alone does not enable tracing.

## Capture a Python skill

```sh
python3 /skill/scripts/oc.py capture --url https://app.example.com \
  --service app-client --output /private/client.jsonl \
  /other/skill/scripts/client.py query 'SELECT id FROM documents LIMIT 5'
```

Use the original app's credentials and actual command names. Supply a Python script, not a shell command. Capture supports `urllib.request` within that process only. Requests to the exact supplied origin's SQL/schema, ordinary record APIs and batch endpoint are timed; auth requests are excluded. Set `--capture-sql` only when intended for the shared trace audience. The output contains no credentials, URL queries, REST bodies or response contents. Store it outside repositories; client output does not rotate automatically.

## Upload and watch

```sh
python3 /skill/scripts/oc.py login --google
python3 /skill/scripts/oc.py ingest /private/server.jsonl --follow
```

Run collection on a machine that can read the source spool. The collector reads active and `.1` files, polling once per second. It compares immutable existing content through SQL before REST writes. Restart after transient failures; unchanged records are skipped on replay. A conflicting ID or malformed line stops ingestion for review. Completed server traces may be lost on source queue overflow, missed rotations or crashes. Do not enable source tracing on ObserveContext itself; collecting its own ingestion would recurse.

## Dashboard

`python3 /skill/scripts/oc.py dashboard` prints a private loopback URL on port 8766. It shows recent requests, 24-hour latency/error summaries and individual span timings, refreshing every five seconds. Login happens in Python and no application token reaches the browser. Forward port 8766 over SSH if needed. Keep the URL private and stop the process with Ctrl-C. Dashboard data is completed-request telemetry after ingestion, not in-flight progress.

`logout` removes only the local cache; it does not revoke server tokens. An operator disables the application account to revoke access. Google Workspace suspension alone does not revoke existing application sessions.
