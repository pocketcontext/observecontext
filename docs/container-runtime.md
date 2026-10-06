# Container runtime

The released container requires private primary S3 storage and a separate
Litestream SQLite replica bucket with separate credentials. One Python entrypoint
replaces the shell startup path. Direct local server development remains supported;
the container does not support `LITESTREAM_DISABLED` or local-file fallback.

Supply all five `OBSERVECONTEXT_S3_` settings: `BUCKET`, `ENDPOINT`, `REGION`,
`ACCESS_KEY_ID`, and `SECRET_ACCESS_KEY`. `FORCE_PATH_STYLE` defaults to `true`.
Supply `LITESTREAM_BUCKET`, `LITESTREAM_PATH`, `LITESTREAM_ACCESS_KEY_ID`, and
`LITESTREAM_SECRET_ACCESS_KEY`, plus the appropriate endpoint and region.
Keep all values in private deployment configuration.

## Explicit initialization

Normal startup requires an existing database or a recoverable replica. A fresh
installation requires a deliberate one-shot `init` invocation of the image with
the intended `/storage` volume and complete storage configuration. It refuses a
nonempty database directory or an existing replica. Then start that same image
normally with the same volume and configuration. Never initialize to conceal a
failed restore. Interrupted initialization leaves a marker that blocks normal
startup; preserve the volume and recover into a fresh destination.

## Recovery and maintenance

Restore runs in a private staging directory. The entrypoint checks SQLite
integrity and the application schema, inventories every PocketBase file field
(including authentication avatars), and reads each referenced S3 object fully.
Missing objects and incomplete responses block installation of the restored
database, including on retries. Generic file fields do not have authoritative
hashes: this verifies readability and response length, not cryptographic identity.
Keep objects for at least the database recovery retention period. Litestream does
not back up primary object bytes.

Frozen startup requires the existing primary and auxiliary databases and a valid
private `maintenance.json`. It verifies persisted S3 configuration, preserves the
freeze, and skips provisioning and restore. For a frozen migration, retain a
consistent SQLite backup of `auxiliary.db` and the maintenance marker alongside
the primary replica; Litestream does not replicate those files. Ordinary writable
recovery initializes auxiliary state through the server. Startup never implicitly
thaws an instance or restores over a surviving database.

Litestream supervises the server under tini. A private IPC initial-sync handshake
must succeed before serving. Graceful termination drains the server then completes
Litestream synchronization. Replication remains asynchronous; sudden host loss can
lose unreplicated commits. Maintain exactly one writer and replica publisher.

## Validation

Run `python3 tests/entrypoint.py` for synthetic safety regressions, including
failed staged restore, missing objects, malformed maintenance state, interrupted
initialization, credential handling and termination during preparation. Run the
README backend checks with the exact server pin, then build the real image and run:

```sh
docker build -t observecontext:ci .
python3 docker/smoke.py config --image observecontext:ci
python3 docker/smoke.py smoke --image observecontext:ci
python3 docker/smoke.py restore --image observecontext:ci
python3 docker/object_storage_smoke.py --image observecontext:ci
```

These drills use isolated MinIO fixtures and synthetic records. They check real
protected uploads, independent file authorization, frozen restart, complete
logical database equality after final synchronization, and empty-volume recovery.

The old deployment is retired. Upgrading source does not authorize a replacement
deployment. See [CI and deployment](ci-and-deployment.md) for publication and
future deployment controls.
