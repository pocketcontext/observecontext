## Packaged CLI and opt-in tracing — 4 October 2026

Deployed source `77fcaf8e407ee7b590297173a0b2e1d42c330ed9` at `https://observe.pocketcontext.com`.
Image `sha256:f95fa6ebde9e14a4d13a6568a78bc10cc5f985d70c1d93df31e7a4fbeee6883f`; server pin `a92b0de5e1b66b6d3b6135b90092d2d6da5f7cc8` is unchanged.
The standalone `observecontext` uv launcher pins package `d10e84bb30ce607c03c62c03a4fdfd596d32ab27`.
Old script entry points are removed; no compatibility wrappers are provided.

[Release CI](https://github.com/pocketcontext/observecontext/actions/runs/37193937394) passed application, browser, container configuration,
smoke and populated recovery gates before publication. Copied remote launchers
passed isolated workflow and tracing tests. A predeployment backup was verified;
the update used the locked operator wrapper. Exact runtime revision, one writer,
existing resource settings and disabled automatic updates were verified.
Public health and anonymous SQL-schema rejection passed; the installed CLI's
live schema check passed. Eight source apps passed a live `SELECT 1` capture
with paired client/server traces and SQL text excluded. No business records
were created; diagnostic traces were uploaded to ObserveContext.

VaultContext was excluded from this migration. A separate VaultContext release
was observed during the window and was left untouched. Five other unrelated
containers retained their IDs, images and settings. The private scaffold records
the coordinated release matrix and verification evidence.

