# ObserveContext data model

Operations and their traces are private to the authenticated owner. Ordinary
users can create operations and append traces only to their own operations.
An operator may grant `users.can_view_all_traces`; this grants read access across
owners, never permission to append to someone else's operation. There is no
sharing policy. Verified Workspace JIT creates ordinary default `users` with the
flag false. Public signup and user-controlled access changes are blocked.

## Records

| Collection | Meaning |
| --- | --- |
| `operations` | Owner-stamped execution groups created before capture |
| `traces` | One completed HTTP request or client attempt, including its reported context and elapsed time |
| `spans` | Ordered measured phases belonging to a trace, suitable for SQL joins and aggregation |
| `user_directory` | Own display identity, or all identities for a read-all viewer |
| `users` | Authentication, excluded from SQL |

POST `/api/collections/operations/records` with `source` (required identifier up
to 100 characters), optional `correlation_id` (up to 128 identifier characters)
and optional `client_key` (32 lowercase hex). The server supplies `id`, `owner`
and `created`. An operation is immutable. Nonempty `(owner, client_key)` is unique
for uncertain-response retry recovery; query the owned key and compare source
and correlation before accepting an existing operation. Empty keys are allowed.
Correlation is diagnostic metadata and never authorizes access or merging across
owners. A single operation may include several correlated HTTP requests.

POST a version-1 envelope to `/api/collections/traces/records` with an ordinary user token. Fields are required `operation` (an existing operation owned by the uploader), `version`, `request_id`, `correlation_id`, `service`, `method`, `route`, `started_at`, `duration_ms`, `status`, `user_id`, `sql`, `rows`, `truncated`, and `spans`. Only `correlation_id`, `user_id`, `sql`, `rows`, `truncated`, and `spans` may be omitted. An omitted numeric or boolean field defaults to zero or false; omitted spans become an empty array.

- `request_id` is 32 lowercase hexadecimal characters. `(created_by, service, request_id)` is unique, so independent owners do not collide.
- `service` is a nonempty identifier of up to 100 letters, digits, underscores, dots, colons or hyphens. `correlation_id` accepts the same alphabet up to 128 characters.
- `method` is uppercase; `route` is the matched path pattern, never a query string or fragment.
- `started_at` is a PocketBase date, normalized to UTC millisecond precision. Times are reported by producers; cross-host clock synchronization is not guaranteed.
- `duration_ms` is a nonnegative finite number up to 86,400,000. HTTP `status` is 100–599; zero means a client transport failure with no response.
- `rows` is a nonnegative safe integer. `truncated` describes the reported result, not sampling.
- `user_id` is reported source context, not an ObserveContext identity relation or evidence of authority.
- `sql` is optional, limited to 16,384 characters. Its contents and all other imported strings are untrusted data. SQL can reveal private business values; capture must be explicitly enabled and the owner and operator-designated read-all viewers can read it.
- `spans` contains at most 128 objects with `name`, `offset_ms`, and `duration_ms`. Names use the service identifier alphabet. Offsets and durations have the trace duration bound; a phase must fit inside the request duration with a one-millisecond rounding tolerance. Phases may overlap and must not be blindly summed.

Ingestion creates the trace and relational spans in one transaction. Each span has `trace`, zero-based `ordinal`, `name`, `offset_ms`, and `duration_ms`; `(trace, ordinal)` is unique. The original spans array remains on the trace for envelope comparison. A failed child write or failed batch rolls back all affected records.

`created_by` and `created` are server-owned. `created_by` identifies the authenticated operation owner, which can report measurements for multiple services. `operation` links every trace to that owned immutable operation. Service and source user labels are not cryptographically verified. Unknown and server-managed submitted fields are rejected. Superusers cannot ingest through this REST interface; use an ordinary identity.

Traces and spans are immutable through REST, including maintenance credentials. There are no ordinary update, correction or deletion operations. The records themselves retain immutable attribution; no separate mutable audit log is needed. Duplicate ingestion returns a validation error. After a network uncertainty, query the existing `(created_by, service, request_id)` and compare its payload before acknowledging success; a conflicting duplicate is an error. Do not generate a new request ID to hide a conflict.

## Access and lifecycle

SQL exports explicit columns from operations, traces, spans and the safe user
directory using a fresh requester-filtered snapshot. REST list/view and realtime
enforce ownership independently. Operation ownership is stamped from the
authenticated identity; `user_id`, service, correlation and client keys are not
proof of identity. Spans inherit visibility from their parent trace.

`trace_authority` is a locked base-table mirror of user disabled/read-all state,
updated in the same transaction as the user and directory. It is policy-only,
unavailable to agent SQL and not independently writable through REST. The users
collection is also excluded from SQL. Superuser maintenance credentials do not
supply ordinary application SQL access. User profile updates, batch writes and
Google createData cannot grant the flag. Changing disabled or read-all state
rotates the token key and revokes existing REST/SQL/realtime sessions; fresh login
uses the new policy. Google suspension alone does not revoke application tokens.

The migration preserves existing traces and spans and creates legacy operations
within each `created_by` uploader: matching nonempty correlation values group
only inside that owner; uncorrelated traces remain separate. It never infers an
owner from reported `user_id`. Existing users default to private access with the
read-all flag false. Existing collectors must create operations and send the
required relation after adopting this schema.

Initial retention is indefinite. Sampling and automated retention are not implemented in ObserveContext; server trace-file rotation bounds local collection storage separately. Capacity planning and a deliberate maintenance retention policy are needed before sustained high-volume use. There are no in-progress traces; records represent completed measurements. Prompt capture, agent-session reconstruction, OpenTelemetry compatibility, DuckDB and caching are outside this initial model.

## Validation

Tests use synthetic users and isolated temporary databases. Integration covers attribution, private cross-user isolation, read-all grant/revocation, owner-only append, legacy migration, immutable REST boundaries, validation, concurrent duplicate ingestion, relational joins, failed child rollback, failed batch rollback, and SQL restrictions. Auth and Google integration tests cover default identity preservation, verified-domain JIT, forged claims, public signup, privilege boundaries and revocation. Realtime tests prove delivery before revocation and after fresh login, and absence of delivery while the old subscription is revoked.

Identity, directory and deployment settings infrastructure was adapted from RaiseContext; the trace schema and ingestion hooks are application-specific.
