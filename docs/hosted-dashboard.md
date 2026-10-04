# Hosted dashboard

ObserveContext serves its dashboard at the application root, including
`https://observe.pocketcontext.com/`. Sign in with your own Workspace Google account.
The browser session is separate from the CLI token cache; no local Python process,
SSH forwarding or public tunnel is needed. The personal `observecontext dashboard` remains
an optional loopback-only view.

## Authentication and visibility

The hosted UI uses the existing default `users` collection and verified Workspace
Google login. It never uses an operator or shared service account. Each data
request runs fixed queries through the existing authenticated, requester-filtered
SQL API with that viewer's ordinary identity. Operation ownership and the
operator-managed read-all role apply independently to each viewer.

The official PocketBase JavaScript SDK uses `LocalAuthStore` with the key
`observecontext.auth`. Sign-in persists across tabs and browser restarts on this
origin. Logout clears the store and private views across tabs; it does not revoke
copies of the bearer token elsewhere. Tokens are accessible to browser JavaScript.
The pinned SDK is vendored from the npm lockfile, served from this origin and
restricted by the dashboard CSP; no remote CDN is used.

Google sign-in uses the SDK's stock realtime OAuth flow and `/api/oauth2-redirect`.
The URL hash stays in the original tab, preserving record and search destinations.
Private reads use the ordinary authenticated filtered SQL API directly. There is
no dashboard cookie session, token proxy or OAuth callback interceptor. The old
cookie sessions are not migrated; existing viewers sign in once after deployment.

Startup validates and renews a persisted token before showing private data.
Visible authenticated tabs renew once a minute; a separate in-memory auth store
prevents delayed renewal from undoing logout or an account switch. Revocation or
expiry clears private content. Account changes cancel old requests and reject late
responses. Existing server permissions still independently protect SQL, REST and
realtime; LocalAuthStore grants no additional access.

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

`BASE_URL` configures the canonical public application origin. Production uses
HTTPS. `OBSERVECONTEXT_DASHBOARD_INTERNAL_URL` and
`OBSERVECONTEXT_DASHBOARD_SESSION_TTL_SECONDS` are obsolete and ignored.
Browser token lifetime is determined by the users auth collection; normal SDK
renewal extends an active session. A server restart no longer signs out viewers.

The image includes the checked-in `web/` assets, SDK license and application hooks.
Rebuild the pinned SDK assets with `npm ci --ignore-scripts && npm run build`.
Keep automatic ONCE updates disabled and use the existing locked graceful-stop
wrapper. No new external service, DNS record, OAuth client or deployment
credentials are required. The optional personal Python dashboard is unchanged.

## Validation

The isolated suite exercises direct SQL ownership, local SDK assets and CSP.
Its real browser mode covers stock SDK OAuth/realtime callback, persistent and
cross-tab authentication, account changes, revocation, delayed refresh/SQL cleanup,
linked destinations, keyboard navigation and mobile timeline geometry:

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
page offset. A pending destination survives Google sign-in in the original tab URL hash.

Operation detail is bounded to 500 trace summaries and 20 detailed measurements;
the UI reports the bound and directs readers to search the operation ID in Traces
for complete paginated browsing. Sources and correlation values remain diagnostic
metadata and never grant visibility. Opening a link performs authenticated reads.
