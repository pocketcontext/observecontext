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

## Read-only migration maintenance

A superuser can inspect `GET /api/context/maintenance` and freeze writes with
`PUT /api/context/maintenance` and `{"readOnly":true,"expectedGeneration":N}`,
using the returned generation. Wait for `state: "read_only"` before taking the
final migration snapshot. Existing writes drain; new mutations return HTTP 503.
Authorized reads, SQL queries, and original-file downloads remain available;
login flows requiring writes can fail. Public submissions are rejected, not queued.

The private `pb_data/maintenance.json` marker persists the freeze across restarts.
Frozen startup preserves stored settings and credentials, skips replica restore
and superuser provisioning, and fails for malformed markers, missing databases
or pending migrations. Preserve the marker alongside the database when migrating.
Thaw explicitly with `readOnly:false` and the current generation; stale generations
return HTTP 409. Freeze does not fence external processes or another host: pause CD
and disable source restart/deployment authority before activating a replacement.

Validate using synthetic temporary data:

```sh
python3 tests/maintenance.py --binary /absolute/path/to/pinned/pocketcontext
python3 tests/maintenance_entrypoint.py
```

Replicated startup waits for Litestream’s private IPC synchronization before serving.
A fresh writable instance initializes its database first; a frozen instance still
requires its existing database. Failed synchronization stops startup. This ensures
Litestream initializes before a quick clean shutdown; replication remains asynchronous.

## Primary file object storage preparation

To store PocketBase API uploads in a private S3-compatible bucket, provide all of
`OBSERVECONTEXT_S3_BUCKET`, `OBSERVECONTEXT_S3_ENDPOINT`,
`OBSERVECONTEXT_S3_REGION`, `OBSERVECONTEXT_S3_ACCESS_KEY_ID`, and
`OBSERVECONTEXT_S3_SECRET_ACCESS_KEY`. R2 uses region `auto` and its account S3
endpoint. Optional `OBSERVECONTEXT_S3_FORCE_PATH_STYLE` is exactly `true` or
`false`, default `true`. Partial configuration fails startup without logging
credentials. A writable restart with stored S3 enabled requires explicit complete
configuration, preventing accidental fallback to local disk. File credentials must
be scoped to the primary bucket; Litestream uses a different bucket and key.
With S3 unconfigured and disabled, existing local development behavior is preserved.

These credentials and the primary file bucket are separate from the
`LITESTREAM_*` SQLite replica configuration. This application currently has no
domain attachment feature; the setting also covers PocketBase file fields such
as the default user avatar. It does not add attachment APIs or change file access
rules. Buckets must remain private and downloads go through PocketBase.

Frozen startup requires complete S3 configuration matching stored settings,
including credentials, and rejects changes before serving. Local frozen starts
remain supported when S3 is disabled. Container preflight rejects partial settings
and sharing either the primary file bucket or access key with Litestream.
Enabling S3 does not copy existing files: reconcile every referenced object and
its checksum before switching a production database. Preserve the maintenance
marker and stop the old writer before thawing the replacement. Litestream
replicates SQLite, not primary bucket contents; plan file retention independently.
These changes are preparation only: they have not been deployed, and existing
files and production databases have not been migrated.

Validate the configuration and frozen-restart contract with:

```sh
python3 tests/object_storage.py --binary /absolute/path/to/pinned/pocketcontext
```

The container release gate also exercises a disposable MinIO service with separate
bucket-scoped file and replica credentials. It creates a synthetic protected-file
collection without changing the application schema, verifies owner/other-user/
anonymous downloads, freezes and restarts, and makes a late upload with a one-hour
replication interval. Recovery compares every logical database row before deleting
the source volume, then verifies original and late-upload bytes on the destination.
A third phase thaws, uploads again and cleanly stops the second instance, deletes
its volume, and checks normal entrypoint recovery into an empty third volume.
This path automatically restores SQLite and initializes auxiliary state, without
a manually copied marker or auxiliary database. Only one writer runs at a time.

For a planned frozen migration, copy `maintenance.json` and a consistent
`auxiliary.db` backup alongside the Litestream-restored `data.db`. Frozen startup
cannot initialize a missing auxiliary database. The test copies auxiliary state
from the stopped synthetic source with SQLite's backup API and verifies it; this
is not a claim that Litestream currently replicates `auxiliary.db`.

```sh
python3 docker/object_storage_smoke.py --image observecontext:ci
```

This gate builds the pinned local MinIO fixture and removes its synthetic
containers, volumes and network. It uses no live bucket or production credentials.

Local validation on 6 October 2026 passed image build, container configuration,
smoke and populated restore, plus the three-stage primary S3 recovery gate and
stale-snapshot/private-copy regression checks. These used the pinned server and
isolated synthetic data; images remain local and production has not been changed.
