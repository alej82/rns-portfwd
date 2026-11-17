## Why

Every TCP connection and UDP flow starts with a Reticulum `Link`, and creating
that Link costs the server a handshake, a proof, memory, and a watchdog thread
while the requester pays almost nothing. An anonymous attacker can flood link
requests, and links that never complete the RTT handshake are not counted by
`--max-links` or `--auth-timeout`, so they survive for several minutes. The
allow-lists added earlier only run after the Link exists; they cannot protect
Link establishment itself.

## What Changes

- The server gains an opt-in `--link-stamp-cost FLOAT` (default `0`, disabled)
  that requires Reticulum's native link-request proof-of-work before a `Link`
  is created. A missing or insufficient stamp makes the request be dropped
  silently, without a Link, proof, or error response.
- The server adds a bounded replay cache of accepted `(ephemeral key, signing
  key)` pairs, so a precomputed valid stamp cannot be reused for the cache TTL.
- The server adds `--max-half-open` (default 64) and `--half-open-timeout`
  (default 60 s) to cap and expire links that have not completed the handshake.
  These work even with stamping disabled and on Reticulum 1.5.x.
- When `--link-stamp-cost > 0`, the server includes `stamp_cost` in its announce
  `app_data` so `rns-portfwd-client` can discover it, including through path
  responses while staying stealthy.
- The client gains automatic link stamping: it reads `stamp_cost` from the
  server's app data, configures Reticulum's link stamping before opening a Link,
  and supports `--link-stamp-cost` as an override and `--no-link-stamp` to
  disable it.
- **Dependency stays `rns>=1.5.7,<2`**: the stamp feature is gated. Configuring
  `--link-stamp-cost > 0` on a Reticulum without native link stamps fails with
  the configuration exit code; a client whose server advertises a cost but whose
  Reticulum cannot generate stamps fails fast with the resolution exit code.
- **BREAKING** when cost is enabled: Reticulum <1.6 clients and clients that do
  not stamp cannot establish a Link. The default remains disabled, so existing
  deployments are unchanged.

## Capabilities

### New Capabilities

- `link-stamping`: server-side link stamp cost, pre-Link validation, replay
  cache, half-open link limits, cost announcement, and client-side discovery and
  automatic stamping, with the 1.5/1.6 feature gate.

### Modified Capabilities

- `public-services`: announce payloads may now carry a `stamp_cost` field, so
  the "announce carries only version, display name, and enumeration flag"
  requirement changes.

## Impact

- `src/rns_portfwd/constants.py`: defaults and caps for cost, half-open limits,
  cache TTL/size.
- `src/rns_portfwd/discovery.py`: stamped destination, announce field, cost
  parsing, feature detection.
- `src/rns_portfwd/server.py`, `cli_server.py`: new flags and wiring before the
  destination is created.
- `src/rns_portfwd/client.py`, `link_manager.py`, `cli_client.py`: app-data
  discovery, transport configuration, overrides and fail-fast errors.
- `README.md`, `AGENTS.md`: flags, security model, compatibility matrix, and the
  announce payload change.
- Unit tests for validation, replay cache, half-open accounting and announce
  parsing; integration tests with a low cost (8) for TCP and UDP, plus rejection
  of a non-stamping client.
- No new dependency; no wire change to `OPEN`, `DATA`, `DGRAM`, `/list` or
  `/probe`. The link request keeps its size and format.
