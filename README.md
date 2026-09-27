# ObserveContext

Request tracing and SQL performance analysis for PocketContext applications. ObserveContext stores completed requests and their timed steps in SQLite, exposes authenticated read-only SQL, and supplies a portable Python skill with a small live dashboard. It does not require Langfuse, OpenTelemetry or another database.

One deployment admits verified Workspace identities. Ordinary users read and append their own operations and traces; an operator-managed `can_view_all_traces` flag grants broader read visibility. Operations, traces and their spans are immutable. The authenticated uploader is recorded separately from the producer-reported application user and service. Traces are diagnostic data, not proof that a producer's claims are authentic. See [the data model](docs/data-model.md).

## Run

Build the PocketContext commit in `POCKETCONTEXT_VERSION` with its declared Go version, CGO and a C compiler. Run the binary from this application directory:

```sh
/path/to/pinned/pocketcontext serve --dir ./pb_data --http 127.0.0.1:8090
```

PocketBase's existing default `users` collection serves humans and their agents. Configure `OBSERVECONTEXT_GOOGLE_CLIENT_ID`, `OBSERVECONTEXT_GOOGLE_CLIENT_SECRET` together and `OBSERVECONTEXT_GOOGLE_WORKSPACE_DOMAIN` for verified Workspace JIT. Use a separate Google Web client; the portable client callback is `http://127.0.0.1:8765/callback`. Without the domain, accounts must be provisioned. Public signup and user-administered privilege changes are blocked. Disabling an account revokes its sessions; Workspace suspension alone does not. Keep credentials and `pb_data` private.

## Collect and inspect

Copy `skills/observecontext/` anywhere, or install it from this repository. The client uses only Python's standard library. Set `OBSERVECONTEXT_URL` and `OBSERVECONTEXT_USER_EMAIL`; then run `oc.py login --google`. Optional `OBSERVECONTEXT_USER_PASSWORD` supports provisioned ordinary accounts. The examples below assume the repository root; use the installed script's absolute path elsewhere.

```sh
python3 skills/observecontext/scripts/oc.py login --google
python3 skills/observecontext/scripts/oc.py check
python3 skills/observecontext/scripts/oc.py capture --url https://crm.example.com --service dealcontext.client --upload /path/to/dc.py sql 'SELECT id FROM organizations LIMIT 5'
python3 skills/observecontext/scripts/oc.py flush
# In another terminal:
python3 skills/observecontext/scripts/oc.py recent --pretty
python3 skills/observecontext/scripts/oc.py dashboard
```

The dashboard binds only to `127.0.0.1:8766`, keeps server tokens in Python, and prints a private local URL. It refreshes every five seconds with the latest 50 operations, grouping measurements by owned operation ID. Each operation shows both durations and shared details; missing or ambiguous measurements remain explicit. The expandable 24-hour summary counts raw trace measurements separately. Displayed timestamps use Europe/Berlin with daylight-saving adjustment. Over SSH, forward port 8766. For an operator-configured authenticated reverse proxy, see [protected development dashboard](docs/dashboard-tunnel.md); the explicit `--public-origin` option is not authentication. It shows completed requests after collection, not in-flight progress. SQL remains the flexible analysis interface:

```sh
python3 skills/observecontext/scripts/oc.py query 'SELECT service,route,avg(duration_ms) AS avg_ms,max(duration_ms) AS max_ms FROM traces GROUP BY service,route ORDER BY max_ms DESC'
```

See [instrumentation](docs/instrumentation.md) for client-requested source traces, account-bound local delivery, and optional legacy file collection. One wrapped invocation creates one owned operation grouping its client/server traces. Tracing is opt-in for each authenticated source request. Other apps must intentionally adopt the pinned server revision and enable the configuration before producing traces; this repository does not change or deploy their server pins.

## Validation

Use synthetic data and isolated temporary databases. Build the exact server pin, then run:

```sh
python3 tests/integration.py --binary /absolute/path/to/pinned/pocketcontext
python3 tests/migration.py --binary /absolute/path/to/pinned/pocketcontext
python3 tests/auth.py --binary /absolute/path/to/pinned/pocketcontext
python3 tests/oauth_integration.py --binary /absolute/path/to/pinned/pocketcontext
python3 tests/realtime_access.py --binary /absolute/path/to/pinned/pocketcontext
python3 tests/deploy.py --binary /absolute/path/to/pinned/pocketcontext
python3 tests/skill.py --binary /absolute/path/to/pinned/pocketcontext
python3 tests/tracing.py --binary /absolute/path/to/pinned/pocketcontext
python3 tests/upload_integration.py --binary /absolute/path/to/pinned/pocketcontext
python3 tests/oauth.py
python3 tests/client.py
python3 tests/upload.py
python3 tests/deploy_workflow.py
```

Container configuration, persistence and populated restore tests gate publication. The public image targets AMD64 and ARM64; release archives include checksums and source/digest metadata. See [deployment](docs/deployment.md) for publication and future ONCE deployment requirements. No production service or cloud resources are provisioned by the local implementation.

## Scope and limits

The initial server instrumentation measures authentication; SQL preparation, execution and result scanning; filtered snapshot waiting, construction and reader setup; and SQL result encoding. REST requests have total server duration, without separate domain-hook or transaction measurements. The client wrapper adds HTTP time including response consumption. It supports Python scripts using `urllib.request`, not arbitrary subprocesses or other networking libraries.

SQL capture is explicit and capped at 16 KiB; SQL literals may contain private data; the uploader and operator-authorized broad viewers can read captured text. Headers, credentials, URL queries, REST bodies and query result values are excluded. No coding-agent prompt or full agent session is collected. Caching, DuckDB and materialized views remain future decisions informed by these measurements.

Source memory buffers expire and may lose telemetry on overflow or restart. The client persists completed pairs in a private, bounded, account-bound queue; `flush` retries operation and trace uploads without changing their IDs. Telemetry failures preserve the wrapped command’s exit status. Legacy server-file collection remains available explicitly. Stored traces have no automatic expiry in this first release. Plan storage and archival before high-volume use. Application authentication/deployment infrastructure was adapted from RaiseContext; the observability schema is independent.
