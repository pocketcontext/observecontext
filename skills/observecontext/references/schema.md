# Trace schema

Authenticated schema and SQL expose requester-filtered tables:

- `operations`: `id`, `owner`, `source`, `correlation_id`, `client_key`, `created`.
- `traces`: `id`, `operation` (operations.id), `version`, `request_id`, `correlation_id`, `service`, `method`, `route`, `started_at`, `duration_ms`, `status`, `user_id`, `sql`, `rows`, `truncated`, `spans` (JSON), `created_by`, `created`.
- `spans`: `id`, `trace` (traces.id), `ordinal`, `name`, `offset_ms`, `duration_ms`.
- `user_directory`: `id`, `name`. Authentication records and permission flags are excluded.

POST an operation with required `source`, optional `correlation_id` and optional 32-hex `client_key`. The server assigns its ID and owner. A nonempty `client_key` is unique per owner, permitting uncertain-create retries. Every trace POST must include its required operation ID; the authenticated uploader must own that operation, even when allowed to view other users' traces.

`created_by,service,request_id` is unique. Every retry lookup includes the authenticated owner; identical producer IDs belonging to another user are independent. `correlation_id` may compare client/server timings within an operation, but is caller-supplied metadata, never an authorization key. `user_id` reports the source application's identity, not an ObserveContext relation. `created_by` identifies the authenticated uploader. Use operation IDs for dashboard drill-down and SQL grouping.

Operations, traces and spans are immutable. A trace POST atomically creates its relational spans; direct span writes and record changes/deletions are refused. Ordinary users see their own operations and traces in SQL, REST and realtime. Only operator-managed `can_view_all_traces` permits broader reads. It grants no authority to append to another operation. Permission changes revoke existing tokens, requiring fresh login.

`started_at` is source UTC time rounded to milliseconds; `created` is ingestion time. `rows` and `truncated` describe source results, not the current analysis query. Durations are finite nonnegative milliseconds bounded to 24 hours; spans <=128; SQL <=16,384 characters. HTTP status is 0 or 100–599. Stored traces do not expire automatically.
