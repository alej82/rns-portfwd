## Why

Today a service is protected only by an optional password. There is no way to
restrict who may use a service even when they know the password, and a public
service is usable by anyone who knows the destination hash. Operators need a
second, per-service authorization layer based on the caller's Reticulum
identity, applied independently of the password (including passwordless
services).

## What Changes

- The server accepts a repeatable `--allow-identity SERVICE:HASH` option that
  adds a 32-hex Reticulum identity hash to that service's allow-list. Repeating
  the flag for the same service adds more identities.
- A service with an allow-list authorizes `OPEN`, `/probe`, and UDP sessions
  only when the Link initiator has identified itself with an allowed identity
  **and** satisfies the service password rule. Public services keep ignoring
  the password but still require an allowed identity.
- A rejected identity is reported exactly like a wrong password: same `OPEN_ERR`
  code, same delay, same failure accounting, same Link teardown behavior.
- The client gains an opt-in persistent identity (`--identity PATH`,
  created with `0600` permissions when missing) and calls
  `Link.identify()` on every Link it establishes. Without the flag the client
  stays anonymous as today and allow-listed services reject it.
- New `rns-portfwd-client identity [--identity PATH]` subcommand prints the
  32-hex identity hash to hand to the server operator.
- `/list` and `/probe` report `auth: true` for a service that has a password
  **or** an allow-list. `/list` keeps showing every service; it does not filter
  entries per caller.
- A public service that also has an allow-list uses
  `--max-connections-per-service` (it is no longer unrestricted) instead of
  `--max-connections-per-public`.
- **BREAKING** (behavioral): clients that do not pass `--identity` cannot use
  allow-listed services; servers that declare no `--allow-identity` behave
  exactly as before.

## Capabilities

### New Capabilities

- `identity-allowlist`: per-service identity allow-lists on the server,
  identity presentation on the client, hash format, enforcement on `OPEN`,
  `/probe` and UDP sessions, indistinguishable rejection, and the identity
  helper subcommand.

### Modified Capabilities

- `public-services`: a public service with an allow-list requires an allowed
  identity, reports `auth: true`, and uses the per-service connection limit.

## Impact

- `src/rns_portfwd/config.py`: `Service.allowed_identities`, allow-list parsing
  and validation, secret-source resolution preserving allow-lists.
- `src/rns_portfwd/registry.py`: `authenticate`/`probe` take the caller identity;
  limit selection uses "restricted" instead of "has password".
- `src/rns_portfwd/server.py`: `remote_identified` callback, deferred
  authorization while the identity is in flight, probe enforcement.
- `src/rns_portfwd/link_manager.py`, `client.py`, `cli_client.py`: persistent
  client identity, `Link.identify()`, `identity` subcommand.
- `src/rns_portfwd/constants.py`: identity wait timeout.
- `README.md`, `AGENTS.md`: new flag, security model, removal of the
  "no allow-list" limitation.
- Unit tests for config, registry, server handlers and link manager; an
  integration test with two client identities (allowed and rejected).
- No new runtime dependency; Reticulum `Link.identify()` is already part of
  `rns>=1.5.7,<2`.
