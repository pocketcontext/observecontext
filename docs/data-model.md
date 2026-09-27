# ObserveContext data model

One deployment is one shared workspace. Every enabled default `users` identity may ingest and read all traces and spans, including captured SQL. Verified Google Workspace JIT grants this ordinary access. Direct public signup is blocked. `users` retains PocketBase's built-in identity; there is no `agents` collection.

## Records

| Collection | Meaning |
| --- | --- |
| `traces` | One completed HTTP request or client attempt, including its reported context and elapsed time |
| `spans` | Ordered measured phases belonging to a trace, suitable for SQL joins and aggregation |
| `user_directory` | Safe authenticated producer IDs and display names |
| `users` | Authentication, excluded from SQL |

POST a version-1 envelope to `/api/collections/traces/records` with an ordinary user token. Fields are `version`, `request_id`, `correlation_id`, `service`, `method`, `route`, `started_at`, `duration_ms`, `status`, `user_id`, `sql`, `rows`, `truncated`, and `spans`. Only `correlation_id`, `user_id`, `sql`, `rows`, `truncated`, and `spans` may be omitted. An omitted numeric or boolean field defaults to zero or false; omitted spans become an empty array.

- `request_id` is 32 lowercase hexadecimal characters. `(service, request_id)` is unique.
- `service` is a nonempty identifier of up to 100 letters, digits, underscores, dots, colons or hyphens. `correlation_id` accepts the same alphabet up to 128 characters.
- `method` is uppercase; `route` is the matched path pattern, never a query string or fragment.
- `started_at` is a PocketBase date, normalized to UTC millisecond precision. Times are reported by producers; cross-host clock synchronization is not guaranteed.
- `duration_ms` is a nonnegative finite number up to 86,400,000. HTTP `status` is 100–599; zero means a client transport failure with no response.
- `rows` is a nonnegative safe integer. `truncated` describes the reported result, not sampling.
- `user_id` is reported source context, not an ObserveContext identity relation or evidence of authority.
- `sql` is optional, limited to 16,384 characters. Its contents and all other imported strings are untrusted data. SQL can reveal private business values; capture must be explicitly enabled and every Workspace member can read it.
- `spans` contains at most 128 objects with `name`, `offset_ms`, and `duration_ms`. Names use the service identifier alphabet. Offsets and durations have the trace duration bound; a phase must fit inside the request duration with a one-millisecond rounding tolerance. Phases may overlap and must not be blindly summed.

Ingestion creates the trace and relational spans in one transaction. Each span has `trace`, zero-based `ordinal`, `name`, `offset_ms`, and `duration_ms`; `(trace, ordinal)` is unique. The original spans array remains on the trace for envelope comparison. A failed child write or failed batch rolls back all affected records.

`created_by` and `created` are server-owned. `created_by` identifies the authenticated producer, which can report measurements for multiple services. Service and source user labels are not cryptographically verified. Unknown and server-managed submitted fields are rejected. Superusers cannot ingest through this REST interface; use an ordinary identity.

Traces and spans are immutable through REST, including maintenance credentials. There are no ordinary update, correction or deletion operations. The records themselves retain immutable attribution; no separate mutable audit log is needed. Duplicate ingestion returns a validation error. After a network uncertainty, query the existing `(service, request_id)` and compare its payload before acknowledging success; a conflicting duplicate is an error. Do not generate a new request ID to hide a conflict.

## Access and lifecycle

SQL exports only explicit columns from traces, spans and the safe user directory. Auth records, credentials and SQLite internals are unavailable. REST list/view and realtime independently require an enabled user. Workspace visibility includes every producer's records; attribution is not a privacy boundary. Disabling a user revokes existing SQL, REST and realtime tokens without deleting their historical identity. Re-enabling requires fresh login. Google suspension alone does not revoke an existing application token.

Initial retention is indefinite. Sampling and automated retention are not implemented in ObserveContext; server trace-file rotation bounds local collection storage separately. Capacity planning and a deliberate maintenance retention policy are needed before sustained high-volume use. There are no in-progress traces; records represent completed measurements. Prompt capture, agent-session reconstruction, OpenTelemetry compatibility, DuckDB and caching are outside this initial model.

## Validation

Tests use synthetic users and isolated temporary databases. Integration covers attribution, shared read access, immutable REST boundaries, validation, concurrent duplicate ingestion, relational joins, failed child rollback, failed batch rollback, and SQL restrictions. Auth and Google integration tests cover default identity preservation, verified-domain JIT, forged claims, public signup, privilege boundaries and revocation. Realtime tests prove delivery before revocation and after fresh login, and absence of delivery while the old subscription is revoked.

Identity, directory and deployment settings infrastructure was adapted from RaiseContext; the trace schema and ingestion hooks are application-specific.
