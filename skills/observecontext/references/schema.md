# Trace schema

Authenticated `/api/context/schema` and `/api/context/query` expose three tables:

- `traces`: `id`, `version`, `request_id`, `correlation_id`, `service`, `method`, `route`, `started_at`, `duration_ms`, `status`, `user_id`, `sql`, `rows`, `truncated`, `spans` (JSON), `created_by`, `created`.
- `spans`: `id`, `trace` (traces.id), `ordinal`, `name`, `offset_ms`, `duration_ms`.
- `user_directory`: `id`, `name`. Authentication records are excluded.

`service,request_id` is unique. `correlation_id` joins client and server measurements; it is optional and client-supplied. `user_id` is a source application's reported identity and does not relate to ObserveContext users. `created_by` is set by the server to the ordinary authenticated uploader. `started_at` is source UTC time rounded down to milliseconds; `created` is ingestion time. The `rows`/`truncated` fields describe source SQL results; they do not describe truncation of your current analysis query. Client traces generally leave them at zero/false.

Every trace and relational span is immutable. POST `/api/collections/traces/records` accepts the version 1 envelope fields up to `spans`, excluding `id`, `created_by`, `created`. The server atomically creates the parent and its spans. Direct span writes and trace changes/deletions are refused. Ordinary Workspace users share SQL/REST/realtime read access and may ingest new traces.

Durations/offsets are finite nonnegative milliseconds bounded to 24 hours; spans <=128; SQL <=16,384 characters (producers cap to bytes); status is 0 or 100–599. The initial release does not automatically expire stored traces.
