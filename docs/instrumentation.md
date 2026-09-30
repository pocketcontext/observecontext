# Client-requested tracing

The normal path is an authenticated skill request, an owner-scoped trace in the source server's short-lived memory buffer, then the same client fetching and uploading that trace alongside its client measurement. The source server never receives ObserveContext credentials. No server trace file or separate collector is needed.

## Source server

The buffer mode is available in the published PocketContext revision recorded in `POCKETCONTEXT_VERSION`. Coordinate and test each source application’s adoption before enabling it. Add this top-level member to its `pocketcontext.json` and restart:

```json
"tracing": {
  "enabled": true,
  "delivery": "buffer",
  "service": "dealcontext.server",
  "captureSql": true,
  "maxBytes": 16777216
}
```

An authenticated ordinary user's request must send `X-Context-Trace: 1` to opt in. SQL text additionally requires `X-Context-Capture-Sql: 1` and server `captureSql: true`. The client sets these headers; ordinary unwrapped requests do not opt in. Health, authentication, guests, superusers and trace retrieval are excluded. Responses expose `X-Context-Request-Id`; the requesting account can retrieve its completed envelope from `GET /api/context/traces/{request_id}` using the same source authentication. Another account cannot retrieve it. Revocation/token-key rotation invalidates access to buffered traces.

The buffer retains traces for at most 120 seconds, with global and per-account count/byte bounds. Restart, expiry and overflow lose buffered telemetry. It is diagnostic data, not durable audit history. Keep ObserveContext's own tracing disabled.

Measured phases include `auth`, `sql.prepare`, `sql.execute`, `sql.scan`, `response.encode`, and filtered-snapshot `snapshot.wait`, `snapshot.build`, `snapshot.reader_init`. SQLite work includes query scanning, not only `sql.execute`. REST currently supplies total server time rather than individual domain hooks. Overlapping spans must not be summed.

## Capture and upload

Sign in to the source app through its own client first. Configure and sign in to ObserveContext separately:

```sh
export OBSERVECONTEXT_URL=https://observe.example.com
export OBSERVECONTEXT_USER_EMAIL=you@example.com
python3 /skill/scripts/oc.py login --google
python3 /skill/scripts/oc.py capture \
  --url https://crm.example.com --service dealcontext.client --upload \
  /deal-skill/scripts/dc.py sql 'SELECT id FROM organizations LIMIT 5'
```

Use the source client's actual command names and normal credentials/cache. Pass a Python script, without a `python3` prefix. The wrapper supports `urllib.request` in that process, not arbitrary subprocesses or other HTTP libraries. Only the exact source origin's SQL/schema, ordinary record APIs and batch endpoint are captured. Authentication requests and arbitrary URL paths are excluded. Authenticated redirects are refused so source credentials cannot be forwarded to another destination. Source trace retrieval uses its own nonredirecting HTTP request and the source token held only in memory.

Add `--capture-sql` only when SQL literals may be retained. Headers, tokens, URL queries, REST bodies and query results are never stored. The wrapper fetches server traces immediately after consuming/closing the source response, with bounded retries for completion races. If unavailable, it reports the omission and retains the client measurement. Source credentials are not saved for later retrieval.

One wrapper invocation creates one ObserveContext operation. All its client and server traces link to that API-assigned operation ID. Each HTTP pair also has a fresh correlation ID for timing comparison, but correlation strings are never access-control keys. The authenticated ObserveContext uploader owns the operation and traces; source user IDs and service labels are reported metadata. Ordinary users see their own uploads. An operator-managed `can_view_all_traces` flag permits broader reads without permission to append to another user's operation.

Client duration covers HTTP through response consumption. Source retrieval and ObserveContext delivery are excluded. Client and server use different clocks; client-minus-server is not pure network latency.

## Scripts using multiple source origins

List every permitted origin explicitly, with a distinct client service label. `--service` labels the shared operation; `--origin` supplies per-origin client labels. For a Python script that calls two applications, capture both origins within the same invocation:

```sh
python3 /skill/scripts/oc.py capture --service workspace.report --upload \
  --origin "crm.client=https://crm.example.com" \
  --origin "tasks.client=https://tasks.example.com" \
  /path/to/your/report.py
```

Mappings accept HTTP(S) origins only, with no credentials, path, query or fragment. Duplicate origins (including equivalent default ports) and duplicate client labels are rejected. The ObserveContext upload origin cannot be a capture source. Unlisted origins receive no opt-in headers or client traces. Trace retrieval stays on the corresponding origin with the same request's authentication; credentials observed on one source origin cannot be reused by the wrapper on another, even when both are allowlisted. This covers HTTP API requests only; it adds no browser, file or realtime instrumentation. Existing `--url ORIGIN --service LABEL` remains the single-origin shorthand.

## Pending delivery

Completed pairs are atomically persisted in a private local queue before upload. Default location is `$XDG_CACHE_HOME/observecontext/pending/<account-key>` (or `~/.cache`), capped at 16 MiB per account. Files have mode 0600 and contain no authentication tokens. Stable operation keys and trace IDs permit exact-content replay after uncertain network responses. Identifiers are scoped by the authenticated owner, including when a broad viewer uploads data.

The wrapper uses a cached ObserveContext identity, or briefly authenticates if none exists. If no identity can be established, it warns and runs the source command; sign in first to enable durable telemetry. Delivery failures do not change the source command's exit status. Source trace retrieval has a 1.5-second request deadline; final upload defaults to a 10-second total deadline with individual HTTP calls bounded to two seconds. `--flush-timeout` changes the final bound.

```sh
python3 /skill/scripts/oc.py flush
```

`flush` verifies the live ordinary identity against the queued account binding. It never replays another user's queue. `--spool DIR` selects the same alternative queue base used by capture; `--timeout` bounds replay. A conflicting ID stops replay and leaves the queued pair intact. Capacity or disk failures are reported without failing the original command; telemetry may then be lost. Source buffers expire and completed stored traces currently have no automatic retention.

## Legacy file collection

Existing file delivery remains supported explicitly. Source tracing with `delivery: "file"` and a private `path` uses bounded JSONL rotation. Legacy `capture --output /private/client.jsonl` produces client JSONL without requesting buffered traces. `oc.py ingest /private/server.jsonl --follow` reads active and `.1` files, with owner-scoped exact-content retries. Envelopes without `operation` receive deterministic import operations, grouping matching correlation IDs. This mode requires access to server files and can lose data through missed rotations; it is not needed for client-requested buffer delivery.

The dashboard shows completed uploaded traces and groups by operation IDs; it polls every five seconds. SQL remains the ad hoc analysis interface.
