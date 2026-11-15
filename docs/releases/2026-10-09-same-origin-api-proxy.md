# Same-origin API proxy (9 October 2026)

**Why:** a visitor whose DNS resolver can't resolve `api.helmterminals.xyz` (seen as `DNS_PROBE_FINISHED_NXDOMAIN`)
saw "Cannot reach the Helm Terminals server" even though the API was healthy. Calling the API through the site's own
domain avoids that: the browser only needs `helmterminals.xyz`, which already loads.

**What changed:** `frontend/server.mjs` forwards `/api/*` to the server named in `API_UPSTREAM`. Only `/api/` is
forwarded, only to that one upstream, without cookies, with an 8 MB body cap and a 60 s timeout. Upstream
`Set-Cookie` and `Access-Control-*` headers are dropped. Without `API_UPSTREAM` nothing is proxied (the old behaviour).
No frontend code changed: `api.ts` already builds URLs from `VITE_API_URL`.

## Turning it on (Railway, frontend service)

1. Add the variable `API_UPSTREAM=https://api.helmterminals.xyz` (or the API service's private Railway address).
2. Change `VITE_API_URL` to `/api` (it is read at **build** time, so redeploy/rebuild the frontend).
3. Check: `https://helmterminals.xyz/api/health/ready` returns `{"ready":true}`.

## Rollback

Set `VITE_API_URL` back to `https://api.helmterminals.xyz` and rebuild. `API_UPSTREAM` can stay; it is unused then.

## Known limit: client IPs

The proxy appends the peer it sees to `X-Forwarded-For`. The API only trusts that header from peers listed in
`TRUSTED_PROXY_CIDRS` (empty today), so per-IP limits see the frontend/edge address rather than each visitor, as they
already see Railway's edge address today. Per-account limits are unaffected. Fixing per-visitor IP limits needs
Railway's verified proxy ranges in `TRUSTED_PROXY_CIDRS`; that is a separate decision.
