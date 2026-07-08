# SOCKS proxy support

Tau uses `httpx` for provider requests, OAuth token refreshes, and startup update checks. `httpx` reads standard proxy environment variables such as `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY`.

## What changed

Issue #221 reported failures when the environment contained a generic SOCKS proxy URL such as:

```bash
ALL_PROXY=socks://127.0.0.1:1080
```

`httpx` does not accept the generic `socks://` scheme. It accepts explicit SOCKS schemes such as `socks5://` and `socks5h://`, and those require the optional SOCKS dependency.

Tau now:

- installs `httpx[socks]` in the base package so `socksio` is available;
- normalizes `socks://...` to `socks5://...` before constructing Tau-owned HTTP clients;
- routes provider clients, OAuth token refresh clients, and update-check fetches through shared helpers in `tau_ai.http`.

## Why `socks://` maps to `socks5://`

The generic scheme does not specify whether DNS lookup should happen locally or through the proxy. Tau treats it as SOCKS5 with local DNS resolution because that is the closest explicit `httpx` scheme and avoids silently changing DNS behavior beyond making the previously invalid URL usable.

Users who need proxy-side DNS resolution should set an explicit `socks5h://` URL.

## Future improvement: avoid temporary environment mutation

The current helper temporarily normalizes proxy environment variables while constructing Tau-owned `httpx` clients. For the synchronous update-check helper, the normalization currently wraps the full `httpx.get(...)` call because `httpx.get` constructs and uses a short-lived client internally.

This is acceptable for the current low-concurrency startup update-check path, but environment variables are process-global state. If Tau later performs more concurrent networking around this helper, another thread or task could observe the normalized proxy value while the request is in progress.

If this becomes a concern, prefer avoiding process environment mutation for request execution:

1. normalize proxy values into local data;
2. construct an explicit `httpx.Client` or `httpx.AsyncClient` with equivalent proxy configuration;
3. perform requests through that client without changing `os.environ` during request execution.

When implementing that, preserve `NO_PROXY` semantics. `httpx` currently handles environment proxy discovery and no-proxy matching internally, so replacing it with explicit mounts/proxy configuration should include tests for:

- `ALL_PROXY=socks://...` normalization;
- `HTTP_PROXY` and `HTTPS_PROXY` handling;
- lowercase proxy env vars;
- `NO_PROXY=*` bypass;
- host/domain/IP `NO_PROXY` entries;
- explicit `socks5://` and `socks5h://` values staying unchanged.
