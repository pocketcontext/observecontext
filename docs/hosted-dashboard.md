# Hosted dashboard

ObserveContext serves its dashboard at the application root, including
`https://observe.pocketcontext.com/`. Sign in with your own Workspace Google account.
The browser session is separate from the CLI token cache; no local Python process,
SSH forwarding or public tunnel is needed. The personal `oc.py dashboard` remains
an optional loopback-only view.

## Authentication and visibility

The hosted UI uses the existing default `users` collection and verified Workspace
Google login. It never uses an operator or shared service account. Each data
request runs fixed queries through the existing authenticated, requester-filtered
SQL API with that viewer's ordinary identity. Operation ownership and the
operator-managed read-all role apply independently to each viewer.

The browser holds only an opaque session cookie. PocketBase bearer tokens and
OAuth PKCE state stay in bounded process memory, not browser JavaScript or local
storage. Production cookies use Secure, HttpOnly, SameSite=Lax and the `__Host-`
prefix. Browser sessions expire after one hour by default and are lost on server
restart. Account disabling, token revocation and read-all role changes invalidate
existing sessions; a fresh login is required. Logout removes the server session
and requires the exact origin and session CSRF token.

Google authorization uses one-use browser-bound state and PKCE. The existing
`/api/oauth2-redirect` callback handles dashboard-prefixed state; normal PocketBase
OAuth callback behavior remains available for other clients. The registered
production redirect and the CLI's `http://127.0.0.1:8765/callback` remain unchanged.
Do not put authentication tokens in dashboard URLs.

## Operation views

The dashboard shows recent operations, client/server pairs, captured SQL and
phase timelines. Each measurement has its own millisecond axis from zero to its
total duration. Bars start at the recorded phase offset and extend for the phase
duration, exposing gaps and overlaps. Vertical markers show zero-duration phases;
labels retain reported offsets and durations, rounded to two decimal places.
A phase extending past the trace's end within the ingestion rounding tolerance is
clipped to the axis. Client and server axes are independent; they do not imply
synchronized clocks. Existing stored traces need no conversion.

Times use Europe/Berlin. Recent operations refresh every five
seconds while the tab is visible. Trace details load on demand; the 24-hour
summary loads when expanded and refreshes at most once a minute automatically.
Private data is cleared from the page when authentication expires or the user
signs out. Trace strings and SQL are rendered as text, never interpreted as HTML.

The dashboard displays already uploaded, completed traces. It does not enable
capture, reveal source credentials or change the uploader's ownership. Existing
client `capture --upload`, `--capture-sql` and `flush` workflows remain unchanged.
ObserveContext self-tracing stays disabled. Overlapping phase durations must not
be summed; client-minus-server time is not pure network latency.

## Configuration and deployment

`BASE_URL` configures the canonical public application origin through stored
PocketBase settings. Production uses HTTPS. The server accesses its own API at
`OBSERVECONTEXT_DASHBOARD_INTERNAL_URL`, defaulting to `http://127.0.0.1:80` in the
container. Only literal loopback origins are accepted. For local development,
set this to the actual bound port and configure the public origin accordingly.
The internal origin is operator configuration, never derived from browser headers.

`OBSERVECONTEXT_DASHBOARD_SESSION_TTL_SECONDS` optionally sets an absolute session
lifetime from 1 to 28800 seconds; the default is 3600. No new external service,
DNS record, OAuth client or deployment credentials are required.

The image includes `web/` assets and application hooks. Keep automatic ONCE
updates disabled and use the existing locked graceful-stop deployment wrapper.
A deployment signs out browser sessions because session state is ephemeral;
ordinary CLI tokens and stored telemetry remain unaffected.

## Validation

The required isolated security suite exercises real OAuth exchanges with a
synthetic provider, independent user sessions, trace ownership, read-all changes,
revocation, expiry, logout, origin/CSRF checks and callback replay rejection:

```sh
python3 tests/dashboard_hosted.py --binary /absolute/path/to/pinned/pocketcontext
```

For browser validation, install Playwright 1.63.0 and Chromium in a separate test
location and pass its module path:

```sh
python3 tests/dashboard_hosted.py --binary /absolute/path/to/pinned/pocketcontext \
  --browser-module /path/to/node_modules/playwright
```

Browser checks and the application/container/restore suites gate image publication.
Live Google consent requires an interactive user's browser; synthetic OAuth tests
do not claim to verify that external account interaction.

## Record navigation

The left sidebar selects Operations or Traces and searches IDs, service, route,
status and correlation values across the viewer's authorized records. Search and
page offset are kept in the URL. Each trace remains a separate row in the Traces
collection, including when many traces share an operation. Permanent
`/dashboard#/operations/<id>` and `/dashboard#/traces/<id>` URLs resolve directly,
independent of the recent list. Trace details link to their parent operation.
Copy record link omits filters; Copy search link preserves collection, text and
page offset. A pending destination survives Google sign-in in per-tab storage;
no authentication tokens are stored there.

Operation detail is bounded to 500 trace summaries and 20 detailed measurements;
the UI reports the bound and directs readers to search the operation ID in Traces
for complete paginated browsing. Sources and correlation values remain diagnostic
metadata and never grant visibility. Opening a link performs authenticated reads.

The 30 September 2026 navigation change passed all mandatory README Python
validation commands on the unchanged a92b0de pin. The actual hosted browser suite
covered operation/trace reload and history, collection search, 51 traces sharing
one operation across two pages, mobile geometry, keyboard access, literal SQL,
CSP, token isolation and session revocation. The authenticated endpoint tests
covered cross-owner denial, malformed IDs, search and offsets. Container gates
remain required in release CI.
