# Workflows

Replace `/skill` with the installed skill directory.

## Inspect an operation

Run `python3 /skill/scripts/oc.py recent --pretty`, then `trace REQUEST_ID --service SERVICE`. Group measurements by their API-assigned `operation` ID. Correlation IDs match HTTP client/server pairs inside the operation but do not establish visibility. SQL already filters for the requester; broad viewers should include operation/owner predicates when selecting records with repeated service/request IDs.

## Capture and upload

Log in to the source app with its own skill first. Separately configure ObserveContext's URL/user and sign in with `oc.py login --google` or an ordinary password account. Its cached identity binds the local telemetry queue.

```sh
python3 /skill/scripts/oc.py capture --url https://app.example.com \
  --service app.client --upload \
  /other/skill/scripts/client.py query 'SELECT id FROM documents LIMIT 5'
```

The source needs buffer-capable PocketContext with `tracing.enabled: true` and `tracing.delivery: "buffer"`. The wrapper requests tracing only for authenticated source calls, retrieves the owning user's trace with the source token held in memory, and uploads client/server measurements under one owned operation per wrapper invocation. No source server files or ObserveContext credentials on the source server are required.

Use `--capture-sql` only when the uploader and authorized broad viewers may read SQL literals. Server capture also requires its `captureSql` setting. Headers, tokens, URL queries, REST bodies and response values are excluded. Capture supports only Python `urllib.request` in-process. Authenticated redirects are refused rather than forwarding source credentials. Existing client output and command exit status are preserved if telemetry fails.

For a script calling multiple applications, use `--service workspace.report --origin crm.client=https://crm.example.com --origin tasks.client=https://tasks.example.com --upload`. All measurements share one operation, with separate client service labels and origin-bound source credentials. Only the explicitly mapped API origins are traced; the ObserveContext destination is rejected.

## Retry pending telemetry

```sh
python3 /skill/scripts/oc.py flush
```

The private queue is account-bound and capped at 16 MiB. It contains completed envelopes and stable operation keys, never credentials. Flush authenticates the same ObserveContext identity and compares duplicate payloads; conflicting IDs stop replay. Other accounts' queues are not replayed. `--spool DIR` selects a custom queue base, and `--timeout` bounds retry work. Capture's final flush defaults to ten seconds. Missing source traces can reflect expiry, overflow, revocation or a request that did not opt in; retrieval cannot be retried indefinitely because source tokens are not persisted.

## Legacy file import

`capture --output /private/client.jsonl SOURCE_SCRIPT ...` retains the old client JSONL path without requesting buffer traces. `ingest /private/server.jsonl --follow` is available for explicitly authorized server file collection; it reads active and `.1` files. Missing operation links receive deterministic owner-scoped legacy import operations. Conflicting IDs or malformed lines stop collection; source rotation may lose data. This mode is optional and separate from normal `--upload`.

## Dashboard

The hosted dashboard is available at your configured `OBSERVECONTEXT_URL`. Sign in there as your own Workspace Google user; the browser session is separate from CLI authentication. Ownership and operator-managed read-all access apply to each viewer independently. No local dashboard process or SSH forwarding is required.

For the optional personal local view: `dashboard` prints a private loopback URL on port 8766 and keeps the application's token in Python. It polls every five seconds for completed uploaded operations, requests and phases. Forward port 8766 over SSH if needed. For a public URL, use only an authenticated proxy and the explicit `--public-origin` option; the option itself provides no authentication. Keep the local URL private and stop with Ctrl-C.

`logout` removes only the local cache. An operator disables an application account to revoke access; Workspace suspension alone does not revoke existing sessions.
