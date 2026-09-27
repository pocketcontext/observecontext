# Container and release

ObserveContext uses the server revision in `POCKETCONTEXT_VERSION`. Its image serves HTTP on port 80 with database-backed `GET /up` and persistent state under `/storage/pb_data`. Tini and Litestream forward termination and finish replication. A missing database restores before startup; inaccessible or corrupt replicas prevent startup. Before accepting requests, the server child requires a successful synchronous database and replica sync through Litestream’s private local control socket. This initializes replication before a fast shutdown can occur. Trace and span records have no file attachments, so the database replica covers application records.

The public source target is `pocketcontext/observecontext`; the public image target is `ghcr.io/pocketcontext/observecontext`. Publication does not deploy a live application. `observe.pocketcontext.com` is the prepared hostname and has not been provisioned by this release. Google OAuth, DNS, R2 and ONCE provisioning require separate authorization.

## Configuration

| Variable | Purpose |
| --- | --- |
| `BASE_URL` | Public HTTPS origin; also restricts browser origins. |
| `OBSERVECONTEXT_SUPERUSER_EMAIL`, `OBSERVECONTEXT_SUPERUSER_PASSWORD` | Paired startup operator credentials; provision separately for this app. |
| `OBSERVECONTEXT_GOOGLE_CLIENT_ID`, `OBSERVECONTEXT_GOOGLE_CLIENT_SECRET` | Paired credentials for a separate Google Web OAuth client. |
| `OBSERVECONTEXT_GOOGLE_WORKSPACE_DOMAIN` | Exact verified Google Workspace domain; unset disables automatic account creation. |
| `OBSERVECONTEXT_TRUSTED_PROXY_HEADER` | Use `X-Forwarded-For` behind ONCE. |
| `OBSERVECONTEXT_RATE_LIMITS` | Image defaults to `true`. |
| `LITESTREAM_BUCKET`, `LITESTREAM_PATH` | Dedicated private bucket and unique prefix, planned as `once-pocketcontext/observecontext`. |
| `LITESTREAM_ACCESS_KEY_ID`, `LITESTREAM_SECRET_ACCESS_KEY` | Replica credentials. |
| `LITESTREAM_ENDPOINT`, `LITESTREAM_REGION` | S3 endpoint and region; R2 uses region `auto`. |
| `LITESTREAM_SYNC_INTERVAL` | Defaults to `10s`; replication is asynchronous. |
| `LITESTREAM_DISABLED` | Exactly `true` disables replication for isolated tests. |
| `SMTP_ADDRESS`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `MAILER_FROM_ADDRESS` | Optional ONCE mail settings. |

During authorized provisioning, store application credentials privately in sibling `once-pocketcontext/.envrc.private` under `COLORS_PAR_APP_OBSERVECONTEXT_*` names. Never commit them. Configure the separate Google client redirects `http://127.0.0.1:8765/callback` and `https://observe.pocketcontext.com/api/oauth2-redirect`. All admitted Workspace users share trace visibility; direct signup remains blocked. Use existing default `users`, including for ingesting agents.

## Release checks

```sh
python3 tests/deploy_workflow.py
docker build -t observecontext:local .
python3 docker/smoke.py config --image observecontext:local
python3 docker/smoke.py smoke --image observecontext:local
python3 docker/smoke.py restore --image observecontext:local
```

Run the application tests in README.md with its pinned server too. Container checks use synthetic traces, isolated volumes and disposable MinIO replicas. They verify persistence, crash recovery and a late write saved by graceful shutdown. Never run restore checks against a production replica.

CI gates image publication on application tests and container configuration, smoke and restore checks. Main publishes native AMD64 and ARM64 images under `latest` and `sha-<commit>` tags. OCI source and revision labels identify the release. A public repository alone does not make GHCR public: set the package visibility to public and verify anonymous manifest, configuration and layer access after the first publication. Record the observed digest and source revision in release evidence.

Each successful main release also publishes public Linux AMD64 and ARM64 container archives in [GitHub Releases](https://github.com/pocketcontext/observecontext/releases), with SHA-256 checksums and source/platform/registry-digest metadata. Download the archive and matching checksum, run `sha256sum --check observecontext-linux-amd64.tar.gz.sha256` (or ARM64), then `docker load --input observecontext-linux-amd64.tar.gz`. The loaded tag is `ghcr.io/pocketcontext/observecontext:sha-<full-source-commit>`. Public archives are independently accessible even if the GHCR package remains private; they do not establish anonymous registry access.

## Prepared deployment

The prepared deployment job is hard-disabled with `if: ${{ false && vars.COLORS_PROFILE != '' }}`; repository or organization variables cannot enable live deployment. Change this condition only after explicit deployment authorization and safe wrapper installation and verification. Keep `COLORS_PROFILE` unset until then. The optional deployment job requires an environment with `SSH_PRIVATE_KEY` and variables `SERVER_IP`, `SERVER_USER`, `SSH_KNOWN_HOSTS`; use a dedicated key and trusted pinned host identity.

ONCE automatic updates must remain disabled. `deploy/deploy-observecontext.py` locks this app, validates exactly one matching container and image, pulls, gracefully stops its writer, requires clean exit, and updates only `observe.pocketcontext.com`. It accepts no arguments. Recovery starts the old container only if it is still the sole matching container. Environment updates must follow the same lock and stop discipline. Updates briefly interrupt availability.

`deploy/install.py` installs the fixed-target wrapper from a trusted copy and restricts an existing app-specific SSH key. It preserves sibling keys and validates a narrowly scoped sudoers entry. Reinstall the wrapper if scaffold provisioning rewrites keys.

Rollback requires stopping the writer first. Use an earlier image only when schema-compatible; otherwise restore a validated backup deliberately while production writers are stopped. Never start a restored copy connected to a live production replica. Replication intervals are not a zero-data-loss guarantee; measure recovery and backup age during deployment acceptance. HTTP `/up` proves database availability, not image identity.
