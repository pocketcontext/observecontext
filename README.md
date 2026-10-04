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

Copy `skills/observecontext/` anywhere, or install it from this repository. The executable requires `uv` and resolves a Python 3.11+ package pinned to a full Git commit; initial installation needs network access. Set `OBSERVECONTEXT_URL` and `OBSERVECONTEXT_USER_EMAIL`; then run `observecontext login --google`. Optional `OBSERVECONTEXT_USER_PASSWORD` supports provisioned ordinary accounts. The examples below assume the repository root; use the installed script's absolute path elsewhere.

```sh
./skills/observecontext/observecontext login --google
./skills/observecontext/observecontext check
./skills/observecontext/observecontext capture --url https://crm.example.com --service dealcontext.client --upload -- dealcontext sql 'SELECT id FROM organizations LIMIT 5'
./skills/observecontext/observecontext flush
# In another terminal:
./skills/observecontext/observecontext recent --pretty
./skills/observecontext/observecontext dashboard
```

The hosted dashboard is served at the application's root URL, including `https://observe.pocketcontext.com/`. Sign in with your own Google Workspace identity. Each viewer sees their own operations unless an operator grants read-all access. The official PocketBase JS SDK stores each viewer’s application token in `LocalAuthStore` (`observecontext.auth`), sharing sign-in and logout across tabs on this origin and retaining sign-in across browser restarts. Tokens are accessible to browser JavaScript. See [hosted dashboard](docs/hosted-dashboard.md) for authentication, deployment and session behavior.

The optional `observecontext dashboard` remains personal and loopback-only at `127.0.0.1:8766`, with credentials in Python and a private printed URL. Forward port 8766 over SSH when using it remotely. The hosted dashboard requires neither that process nor port forwarding. Both show completed operations, paired client/server measurements and Europe/Berlin timestamps. For the legacy protected development proxy, see [dashboard tunnel](docs/dashboard-tunnel.md).

SQL remains the flexible analysis interface:

```sh
./skills/observecontext/observecontext query 'SELECT service,route,avg(duration_ms) AS avg_ms,max(duration_ms) AS max_ms FROM traces GROUP BY service,route ORDER BY max_ms DESC'
```

See [instrumentation](docs/instrumentation.md) for client-requested source traces, account-bound local delivery, and optional legacy file collection. One wrapped invocation creates one owned operation grouping its client/server traces. Scripts calling multiple applications can repeat `--origin CLIENT_SERVICE=HTTP_ORIGIN` to capture explicit application origins with separate labels and credentials; `--url` remains the single-origin shorthand. Tracing is opt-in for each authenticated source request. Other apps must intentionally adopt the pinned server revision and enable the configuration before producing traces; this repository does not change or deploy their server pins.

## Python package and launcher

The package is `observecontext-client`; its console command is `observecontext`.
The implementation, schema snapshot and dashboard asset live in
`src/observecontext_client/`. No short aliases or old script entry points remain.
For source development, use `uv run --project . observecontext --help` and
`uv build`. Client packages pin the reusable library to a tested full Git revision.

Publish a tested package commit first, then update the standalone launcher's
`rev` to that published commit. A launcher cannot pin its own containing commit.
The portable skill test builds a wheel and substitutes its local path in a copied
launcher; release acceptance must additionally run the unchanged published launcher.

## Validation

Use synthetic data and isolated temporary databases. Install the locked browser dependency with `npm ci --ignore-scripts`, run `npm run build`, and verify the vendored SDK is unchanged with `git diff --exit-code -- web/pocketbase.es.mjs web/pocketbase.LICENSE.md`. Build the exact server pin, then run:

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
python3 tests/instrumentation.py
python3 tests/deploy_workflow.py
python3 tests/dashboard_hosted.py --binary /absolute/path/to/pinned/pocketcontext
```

With sibling application checkouts available, run the optional cross-application acceptance suite against their current configurations and actual portable clients:

```sh
python3 tests/app_clients.py --workspace .. --binary /absolute/path/to/pinned/pocketcontext
```

It checks wrapped SQL, explicit SQL disclosure, filtered-snapshot phases, and TaskContext REST and batch writes for the listed active applications. Archived applications are excluded. This workspace suite is separate from standalone CI because it requires sibling repositories. Independent multi-origin coverage in `tests/upload.py` checks shared operations, separate credentials, service labels and origin restrictions without sibling applications.

Container configuration, persistence and populated restore tests gate publication. The public image targets AMD64 and ARM64; release archives include checksums and source/digest metadata. See [deployment](docs/deployment.md) for publication and future ONCE deployment requirements. Production release evidence is recorded in the private ONCE deployment scaffold.

## Scope and limits

The initial server instrumentation measures authentication; SQL preparation, execution and result scanning; filtered snapshot waiting, construction and reader setup; and SQL result encoding. REST requests have total server duration, without separate domain-hook or transaction measurements. The client wrapper adds HTTP time including response consumption. It launches packaged Context commands normally; each source package explicitly activates its installed instrumentation library. Unsupported commands run but report that capture was not confirmed. Other networking libraries are outside coverage.

SQL capture is explicit and capped at 16 KiB; SQL literals may contain private data; the uploader and operator-authorized broad viewers can read captured text. Headers, credentials, URL queries, REST bodies and query result values are excluded. No coding-agent prompt or full agent session is collected. Caching, DuckDB and materialized views remain future decisions informed by these measurements.

Source memory buffers expire and may lose telemetry on overflow or restart. The client persists completed pairs in a private, bounded, account-bound queue; `flush` retries operation and trace uploads without changing their IDs. Telemetry failures preserve the wrapped command’s exit status. Legacy server-file collection remains available explicitly. Stored traces have no automatic expiry in this first release. Plan storage and archival before high-volume use. Application authentication/deployment infrastructure was adapted from RaiseContext; the observability schema is independent.
