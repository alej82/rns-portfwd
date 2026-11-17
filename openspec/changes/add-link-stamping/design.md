## Context

See `proposal.md` and `specs/`. The relevant Reticulum facts, verified against
the installed RNS 1.6.0 and its source:

- `RNS/Stamps.py` provides `stamp_valid(stamp, target_cost, material)` and the
  native link-request `key stamp`.
- `RNS.Link.validate_request()` validates the request keyset and, when
  `RNS.Transport.LR_STAMPS_ENFORCED` is set, rejects insufficient stamps before
  creating the `Link` (`Link.py:186-237`). The proof, registration and watchdog
  start after that point.
- `Destination.incoming_link_request()` is the single application-overridable
  entry point before the Link exists; there is no link-request callback.
- `RNS.Transport` exposes `ALWAYS_STAMP_LRS`, `STAMP_LRS`, `LR_COST` and
  `LINK_COST_CAP = 40`; the incoming `Link.establishment_timeout` is set before
  its watchdog starts, and the watchdog reads it again on each loop.
- The installed dependency floor is `rns>=1.5.7,<2`; `RNS/Stamps.py` does not
  exist in 1.5.7, and `Destination.set_link_cost` is 1.6-only.

## Goals / Non-Goals

**Goals:**

- Make anonymous link-request floods pay client CPU before any server-side Link
  object, proof or watchdog exists.
- Bound the half-open link population that escapes `--max-links` and
  `--auth-timeout`.
- Keep the default deployment byte-for-byte compatible and keep the dependency
  floor at 1.5.7.
- Let `rns-portfwd-client` discover and apply the cost automatically.

**Non-Goals:**

- Adding an application-level stamp in `OPEN` or a `/stamp` request. Neither
  protects Link establishment, and both add protocol compatibility risk.
- Exempting allow-listed identities from stamping; native validation precedes
  identity, and we will not add a second post-Link proof in this change.
- Making generic Reticulum clients stamp via `STAMP_DESTINATIONS`; only our
  client consumes the `stamp_cost` app-data field.
- Rate limiting per source/interface, token buckets, or transport-node
  enforcement.

## Decisions

### Feature gate instead of raising the dependency

The project keeps `rns>=1.5.7,<2`. Native stamping is detected lazily:

- `RNS.Stamps` and `Destination.set_link_cost` are probed only when
  `--link-stamp-cost > 0`; missing capabilities raise `ConfigError` (exit 2)
  with an `rns>=1.6` message before the destination is created.
- Half-open limits use only APIs present in 1.5.7 (`Destination.incoming_link_request`,
  `Link.status`, `Link.establishment_timeout`) and therefore work on both
  versions with cost `0`.
- The client probes `RNS.Transport.ALWAYS_STAMP_LRS`/`LR_COST` only when it
  actually needs to stamp; if they are absent, it raises `ResolutionError`
  (exit 4) before opening a Link, instead of waiting for a silent drop.

This matches the chosen rollout: servers can adopt half-open limits now, and
stamping becomes available wherever Reticulum 1.6 is installed.

### Pre-Link validation lives in a Destination subclass

`discovery.make_in_destination()` returns a `StampedDestination` with the stamp
cost, cache and half-open settings. `incoming_link_request(data, packet)`:

1. Calls a pure `LinkRequestGuard.check(data, links)` helper that applies, in
   order: request-length sanity, half-open cap, native stamp validation, replay
   cache. On any failure it returns without calling `super()`, so Reticulum
   never creates the Link, never proves it, and never starts its watchdog.
2. Calls `super().incoming_link_request(data, packet)` on success, then clamps
   the newly appended Link's `establishment_timeout` to `--half-open-timeout`.

Separating `LinkRequestGuard` keeps the policy unit-testable without constructing
RNS destinations or packets. The subclass only wires the guard to Reticulum.

We deliberately do **not** set `RNS.Transport.LR_STAMPS_ENFORCED`: doing so
would make validation global and lose the replay cache. The destination still
calls `set_link_cost(cost)` when available so `get_link_cost()` and external
tooling see the real requirement; native enforcement stays off.

### Half-open checks are cheap and run before the stamp

Counting `destination.links` by `PENDING`/`HANDSHAKE` costs O(n) over a list
already bounded by accepted Links; doing it before the stamp avoids spending
even one SHA-256 on a request that cannot be accepted. The count and the replay
cache share one lock. The lock is released before `super()` so Reticulum
callbacks never run under it; a small concurrent overshoot of the cap is
acceptable because the cap is a memory guard, not an exact quota.

`--max-half-open 0` disables the count, `--half-open-timeout` is clamped only
when positive (validated as > 0), and the default 60 s is above the client's
default 30 s `--connect-timeout` while being far below Reticulum's
`6*hops + 360 s` default.

### Replay cache: bounded LRU with TTL

`LinkRequestGuard` stores accepted `(pub, sig_pub)` tuples in an `OrderedDict`
with `TTL = 600 s` and `MAX_ENTRIES = 16384`. On insert it prunes expired
entries and evicts the oldest entry when full. Reserving the pair happens before
`super()`; if `super()` did not append a Link, the reservation is removed so a
transient RNS rejection can be retried.

TTL is a deliberate trade-off: indefinitely remembering every pair would grow
without bound, while a 10-minute window makes a precomputed stamp reusable only
after the cache forgets it. The cache is per server process and resets on
restart, which is acceptable because a restart also re-arms the cost and clients
reconnect with fresh ephemeral keys.

### Announce field is additive app data

`announce_data(name, auth, stamp_cost=None)` and
`set_default_app_data(..., stamp_cost=...)` add `"stamp_cost"` only when the
cost is positive. `parse_announce_data()` keeps returning the raw dict, and a
new `parse_stamp_cost()` rejects booleans, non-numbers, and values outside
`[0, 40]`, returning `0` for invalid input. Path responses reuse the announce
app data, so the cost is discoverable even without `--announce`.

### Client applies the cost once, before any Link

`configure_link_stamping(server_hash, override, disabled, logger)` runs after
`resolve_identity()` in `PortfwdClient.run`, `run_list`, and `run_probe`. It
reads `RNS.Identity.recall_app_data(server_hash)`, parses `stamp_cost`, applies
`--link-stamp-cost`/`--no-link-stamp`, then sets
`RNS.Transport.ALWAYS_STAMP_LRS = True` and `RNS.Transport.LR_COST = cost`
before the first `Link` is created. Reconnects reuse the same process-wide
settings.

The server already advertises the cost in its path response, so a stealth
server needs no periodic announce. `--link-stamp-cost` covers deployments that
hide the field; `--no-link-stamp` is a diagnostic and is mutually exclusive with
the override.

### No wire or application-protocol changes

The native stamp lives in the existing link request keyset; `OPEN`, `DATA`,
`DGRAM`, `/list`, and `/probe` are untouched. This also avoids Reticulum
`Channel`'s unknown-message-type behavior, which throws and does not advance the
sequence.

## Risks / Trade-offs

- **Replay within the TTL** → the cache blocks immediate reuse; after 10 minutes
  a precomputed pair can be replayed. Mitigation: TTL/size are constants, and
  half-open caps still bound the accepted population.
- **Cache memory** → 16384 entries × two 32-byte keys plus bookkeeping, roughly
  a few MiB; bounded by construction.
- **Half-open cap false positives** → a burst of legitimate reconnects could be
  dropped. Mitigation: default 64 is generous for this server's `--max-links`
  32, and drops are silent only at `debug`; operators can raise the cap.
- **Slow hardware timeouts** → cost 16 can approach Reticulum's 15 s stamp
  timeout on slow ARM/macOS. Mitigation: default is disabled; documented range
  and recommended 12.5; our client fails fast if it cannot generate stamps.
- **Global transport flags** → `ALWAYS_STAMP_LRS`/`LR_COST` are process-wide.
  This client supports one server per process, so the scope is exactly right;
  `--no-link-stamp` restores the previous behavior.
- **No generic-client interop** → RNS clients that do not read our app data will
  not stamp and will fail to connect while cost is positive. Mitigation:
  documented compatibility matrix and default `0`.
- **Allow-list does not exempt stamps** → operators may expect allow-listed
  users to skip the cost. Mitigation: documented; the native mechanism cannot
  know the identity pre-Link.
- **1.5.x feature gap** → a 1.5.x client sees `stamp_cost` but cannot stamp; it
  fails fast with an explicit error, but it also cannot use a stamping server
  until upgraded. Documented.
- **Spec archive ordering** → the `public-services` delta builds on the
  allowlist delta's `auth` wording and adds `stamp_cost`; archive the allowlist
  change before this one (or re-sync the main spec) so one delta does not undo
  the other.

## Migration Plan

1. Ship with `--link-stamp-cost 0` (default). Half-open caps become active and
   are safe on 1.5.x.
2. Upgrade clients to a version that understands `stamp_cost`.
3. Enable `--link-stamp-cost 12.5` (or 8–14) server-side and verify clients
   connect; keep `--no-link-stamp` for diagnosis.
4. Rollback: set the cost back to `0`; no protocol or storage migration is
   needed.

## Open Questions

None; the dependency gate, scope, discovery and default cost were decided
before this design.
