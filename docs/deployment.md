> The old deployment is retired. Follow [container runtime](container-runtime.md)
> and [CI and deployment](ci-and-deployment.md) for the current architecture.

# Container and release

ObserveContext uses the server revision in `POCKETCONTEXT_VERSION`. Its image serves HTTP on port 80 with database-backed `GET /up` and persistent state under `/storage/pb_data`. Tini and Litestream forward termination and finish replication. A missing database restores before startup; inaccessible or corrupt replicas prevent startup. Before accepting requests, the server child requires a successful synchronous database and replica sync through Litestream’s private local control socket. This initializes replication before a fast shutdown can occur. Trace and span records have no file attachments, so the database replica covers application records.

The public source target is `pocketcontext/observecontext`; the public image target is `ghcr.io/pocketcontext/observecontext`. The old `observe.pocketcontext.com` deployment is retired. Image publication does not authorize a replacement deployment. New cloud provisioning remains a separate operator action.

## Configuration

| Variable | Purpose |
| --- | --- |
| `BASE_URL` | Public HTTPS origin; also restricts browser API origins and configures the OAuth origin. |
| `OBSERVECONTEXT_SUPERUSER_EMAIL`, `OBSERVECONTEXT_SUPERUSER_PASSWORD` | Paired startup operator credentials; provision separately for this app. |
| `OBSERVECONTEXT_GOOGLE_CLIENT_ID`, `OBSERVECONTEXT_GOOGLE_CLIENT_SECRET` | Paired credentials for a separate Google Web OAuth client. |
| `OBSERVECONTEXT_GOOGLE_WORKSPACE_DOMAIN` | Exact verified Google Workspace domain; unset disables automatic account creation. |
| `OBSERVECONTEXT_TRUSTED_PROXY_HEADER` | Use `X-Forwarded-For` behind ONCE. |
| `OBSERVECONTEXT_RATE_LIMITS` | Image defaults to `true`. |
| `OBSERVECONTEXT_S3_BUCKET`, `OBSERVECONTEXT_S3_ENDPOINT`, `OBSERVECONTEXT_S3_REGION`, `OBSERVECONTEXT_S3_ACCESS_KEY_ID`, `OBSERVECONTEXT_S3_SECRET_ACCESS_KEY` | Required private primary file storage; separate bucket and credentials from replicas. |
| `LITESTREAM_BUCKET`, `LITESTREAM_PATH` | Dedicated private bucket and unique prefix, planned as `once-pocketcontext/observecontext`. |
| `LITESTREAM_ACCESS_KEY_ID`, `LITESTREAM_SECRET_ACCESS_KEY` | Replica credentials. |
| `LITESTREAM_ENDPOINT`, `LITESTREAM_REGION` | S3 endpoint and region; R2 uses region `auto`. |
| `LITESTREAM_SYNC_INTERVAL` | Defaults to `10s`; replication is asynchronous. |
| `LITESTREAM_DISABLED` | Unsupported; container replication is mandatory. |
| `SMTP_ADDRESS`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `MAILER_FROM_ADDRESS` | Optional ONCE mail settings. |

During authorized provisioning, store application credentials privately in sibling `once-pocketcontext/.envrc.private` under `COLORS_PAR_APP_OBSERVECONTEXT_*` names. Never commit them. Configure the separate Google client redirects `http://127.0.0.1:8765/callback` and `https://observe.pocketcontext.com/api/oauth2-redirect`. Each admitted Workspace user sees only their own operations, traces and spans. An operator may grant `users.can_view_all_traces` for read-all access; the flag never grants append access to another owner. Direct signup and user-managed privileges remain blocked. Use existing default `users`, including for ingesting agents.

The source application retains a short-lived bounded trace buffer and needs no ObserveContext credentials. Its authenticated client retrieves its own traces and uploads using a separate ObserveContext login. There is no central collector. The authenticated hosted dashboard uses the existing application origin. The personal localhost dashboard remains optional; neither needs a separate hostname or tunnel. Enable `tracing.delivery: "buffer"` separately in each source application after adopting and testing the server pin. Keep ObserveContext self-tracing disabled.

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

## Deployment status

The old deployment and local deployment wrappers are retired. Future deployment
requires explicit authorization and the guarded architecture described in
[CI and deployment](ci-and-deployment.md). No existing source configuration
should be treated as permission to provision or restart a production instance.

Keep one writer and replica publisher. Rollback requires stopping the writer
first; restore into isolated storage and never attach a recovery instance to an
active production replica. `/up` proves database availability, not image identity.
