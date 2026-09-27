# Protected development dashboard

The dashboard always listens on `127.0.0.1`; its default accepts only the local
Host and Origin. `--public-origin https://observe-dev.example.com` additionally
accepts that exact origin. It does **not** authenticate viewers. The random URL
is a local capability, not a substitute for public access control. All viewers
use the dashboard's ObserveContext account and inherit its visibility, regardless
of their own identity. The normal deployment uses a personal localhost dashboard;
a shared tunnel delegates that account's trace access to every allowed viewer.
Tokens remain in the Python process. Keep both application APIs on loopback.

For a Cloudflare named tunnel, create a self-hosted Access application for the
exact dashboard hostname before publishing its DNS route. Configure an Allow
policy for the intended verified Workspace users, with no bypass/public policy.
Prefer the organization's Google identity provider; account Access onboarding,
identity provider setup and appropriate API permissions are prerequisites.
Set a short session duration appropriate to the development test.

Require Access JWT validation in cloudflared itself as well as the edge policy.
For a locally managed named tunnel, use an ingress-specific rule like this:

```yaml
tunnel: TUNNEL_UUID
credentials-file: /private/test/config/tunnel-credentials.json
ingress:
  - hostname: observe-dev.example.com
    service: http://127.0.0.1:8766
    originRequest:
      access:
        required: true
        teamName: YOUR_TEAM_NAME
        audTag:
          - YOUR_ACCESS_APPLICATION_AUDIENCE
  - service: http_status:404
```

Use the team name without `.cloudflareaccess.com`. Do not rewrite Host to
localhost, disable Access validation or add API ingress rules. Equivalent
remotely managed tunnel configuration must preserve these per-ingress settings.
Keep tunnel credentials/configuration and the dashboard's random URL private.

```sh
python3 skills/observecontext/scripts/oc.py dashboard \
  --public-origin https://observe-dev.example.com
cloudflared tunnel --config /private/test/config/tunnel.yml ingress validate
cloudflared tunnel --config /private/test/config/tunnel.yml run
```

Before sharing the URL, verify that an unauthenticated external request cannot
read HTML, data or trace details, and that forged Access headers/JWTs fail.
Verify an allowed user can sign in, poll data and inspect a trace, while a user
outside the policy cannot. Check the connector's Access configuration and
confirm no alternate hostname/path reaches the origin without the same gate.
The dashboard's unit tests cover origin enforcement; they do not prove a live
Cloudflare policy or JWT validation. Do not describe the tunnel as verified
until those external checks pass. Stop the connector and remove the dedicated
DNS/tunnel/Access resources when the test ends.

Cloudflare references: [origin Access settings](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/origin-parameters/#access-settings)
and [self-hosted applications](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/).
