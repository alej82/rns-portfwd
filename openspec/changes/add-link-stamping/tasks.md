## 1. Constants and configuration

- [x] 1.1 Add constants for link stamping: `DEFAULT_LINK_STAMP_COST = 0.0`, `MAX_LINK_STAMP_COST = 40.0` (or read `RNS.Transport.LINK_COST_CAP` with fallback), `LINK_STAMP_CACHE_TTL = 600.0`, `LINK_STAMP_CACHE_MAX = 16384`, `DEFAULT_MAX_HALF_OPEN = 64`, `DEFAULT_HALF_OPEN_TIMEOUT = 60.0`
- [x] 1.2 Add a `validate_link_stamp_cost(value)` helper accepting `0 <= cost <= MAX_LINK_STAMP_COST` and rejecting booleans, non-numeric and NaN; unit-test 0, 12.5, 40, negative, 40.1, `NaN`, text and bool
- [x] 1.3 Add `stamp_supported()` / `stamp_generation_supported()` capability probes in `discovery.py` that check for `RNS.Stamps`, `Destination.set_link_cost`, and `RNS.Transport.ALWAYS_STAMP_LRS` without importing optional modules at startup; unit-test the absent-capability branches with monkeypatched modules/attributes
- [x] 1.4 Add `--link-stamp-cost` (default 0), `--max-half-open` (default 64, `0` unlimited), and `--half-open-timeout` (default 60, positive) to `cli_server.py` with validation and help/examples; unit-test parser acceptance and exit code 2 for every invalid value
- [x] 1.5 Reject `--link-stamp-cost > 0` with exit code 2 and an `rns>=1.6` message when either native stamp capability is missing, before `PortfwdServer` is constructed; unit-test both missing capabilities

## 2. Pre-Link guard

- [x] 2.1 Implement `LinkRequestGuard(stamp_cost, cache_ttl, cache_max, max_half_open)` with `check(data, links)` applying, in order: payload length sanity, half-open count (`PENDING`/`HANDSHAKE`), native stamp validation (`RNS.Stamps.stamp_valid` over `destination_hash + sig_pub`), and replay-cache membership; return a boolean and a debug-only reason
- [x] 2.2 Implement the replay cache as an `OrderedDict` keyed by `(pub, sig_pub)` with TTL pruning, oldest-entry eviction at the size limit, a lock around check/reserve, and a `release(pair)` path for requests RNS rejects; unit-test first use, immediate replay, TTL expiry, eviction, concurrency with threads, and release-on-failure
- [x] 2.3 Implement half-open counting and the silent drop at `--max-half-open`, including `0` as unlimited; unit-test counts at `cap-1`, `cap`, `cap+1`, mixed statuses, and an empty list
- [x] 2.4 Make the guard a no-op for stamping when cost is `0` while still enforcing half-open limits; unit-test that no `RNS.Stamps` call occurs with cost `0`
- [x] 2.5 Unit-test that invalid, short, malformed and insufficient stamps never reach `super().incoming_link_request` and produce no `Link`-side effect

## 3. Destination and server wiring

- [x] 3.1 Add `StampedDestination(RNS.Destination)` in `discovery.py` overriding `incoming_link_request`: call the guard, return silently on rejection, call `super()` on success, and clamp the newly appended Link's `establishment_timeout`; keep all inherited destination behavior (`set_max_request_size`, callbacks, requests)
- [x] 3.2 Extend `make_in_destination()` to accept the stamp/half-open settings and return `StampedDestination`; call `set_link_cost(cost)` when available and only when cost is positive
- [x] 3.3 Add `link_stamp_cost`, `max_half_open` and `half_open_timeout` to `PortfwdServer` (defaults from constants), pass them to `make_in_destination()`, and keep `services`/registry/allow-list wiring unchanged
- [x] 3.4 Ensure the destination subclass works on Reticulum 1.5.x with cost `0` (no `RNS.Stamps` import, no `set_link_cost` call) and unit-test the 1.5-shaped capability monkeypatch path
- [x] 3.5 Log rejected link requests only at `debug` and never log the received stamp value, keys, required cost, or service names; unit-test that a rejection produces no warning/error records

## 4. Announce and cost discovery

- [x] 4.1 Extend `announce_data(name, auth, stamp_cost=None)` and `set_default_app_data()` to include `"stamp_cost"` only when the cost is positive; keep existing call sites working without the argument
- [x] 4.2 Add `parse_stamp_cost(app_data)` returning `0` for missing/invalid/boolean/out-of-range values and the float otherwise; unit-test 0, 8, 12.5, 40, 40.1, negative, text, bool, missing dict/key
- [x] 4.3 Pass the configured cost to `set_default_app_data()` in `PortfwdServer.start()`; unit-test that cost 0 omits the field and cost 12.5 includes it, while service names/identity hashes remain absent
- [x] 4.4 Verify the field also appears in path responses (they reuse app data) and that `parse_announce_data()` still accepts payloads with and without the new key

## 5. Client stamping

- [x] 5.1 Implement `configure_link_stamping(server_hash, override, disabled, logger)` in `discovery.py`: resolve the effective cost from the override or `RNS.Identity.recall_app_data`, validate/clamp it, fail fast with `ResolutionError` when generation is unavailable, and set `RNS.Transport.ALWAYS_STAMP_LRS = True` plus `LR_COST = cost` before any Link
- [x] 5.2 With effective cost `0`, explicitly leave/restore `ALWAYS_STAMP_LRS` false so an anonymous client does not stamp by accident; unit-test both paths and that a missing app data leaves behavior unchanged
- [x] 5.3 Call `configure_link_stamping()` after `resolve_identity()` in `PortfwdClient.run`, `run_list` and `run_probe`, covering reconnects; unit-test one call per command and the order (before `_connect_once`/Link creation)
- [x] 5.4 Add client flags `--link-stamp-cost FLOAT` and `--no-link-stamp` (mutually exclusive) to the global and common client parsers; validate the cost with the shared helper; unit-test flag placement before/after each subcommand, conflicts (exit 2), and propagation
- [x] 5.5 Unit-test fail-fast behavior when the server advertises a positive cost but `ALWAYS_STAMP_LRS`/`LR_COST` or `RNS.Stamps` is missing (exit 4, no Link)
- [x] 5.6 Update client help/examples with the two flags and document that a 1.5.x client cannot connect to a stamping server

## 6. Documentation

- [x] 6.1 Update `README.md`: `--link-stamp-cost`, `--max-half-open`, `--half-open-timeout`, client `--link-stamp-cost`/`--no-link-stamp`, announce `stamp_cost`, cost guidance (disabled default, 12.5, 14–16 range), compatibility matrix (RNS 1.5.x/1.6, generic clients, stealth path responses) and half-open behavior
- [x] 6.2 Update `AGENTS.md`: stamping is pre-Link and silent, replay cache and TTL, half-open caps, announce field, client auto-stamping/fail-fast, dependency gate, and that allow-lists do not exempt stamps
- [x] 6.3 Update the security notes and limitations: link-request abuse model, what stamping does and does not protect, `ps`/privacy non-impact, and the `--no-link-stamp` diagnostic risk
## 7. Integration and verification

- [x] 7.1 Extend `tests/integration/harness.py` with helpers to start a stamping server (`--link-stamp-cost 8`) and a client that can force `--no-link-stamp`; keep the isolated `rnsd`
- [x] 7.2 Cover TCP and UDP forwards with automatic stamping end to end, including `list` and `probe` over the stamped Link
- [x] 7.3 Cover rejection: a `--no-link-stamp` client and a client with a lower override fail to establish a Link, touch no backend, and report the documented exit code; confirm the server did not run any `OPEN` handler
- [x] 7.4 Cover half-open limits with fake Links in unit tests; the integration path is not exercised because it needs crafted link requests, as documented in the design
- [x] 7.5 Cover the announce/path-response `stamp_cost` field in unit tests and prove client discovery through an integration connection; confirm cost `0` servers advertise no field and old clients still work
- [x] 7.6 Run `python3 -m pytest tests/unit -q`, the full `python3 -m pytest -q`, and `ruff check src tests`; all must pass
- [x] 7.7 Run `openspec validate add-link-stamping --strict` and fix any formatting issue

## 8. Review follow-ups

- [x] 8.1 Detect new Links by object identity instead of list length, so a concurrent link close cannot release a valid replay reservation or skip the timeout clamp
- [x] 8.2 Reject non-finite `--half-open-timeout` values (`nan`, `inf`, `-inf`) and document Reticulum's ~5 s watchdog granularity
- [x] 8.3 Pin `Destination.set_link_cost` even at cost `0`, so a Reticulum-wide `enforce_stamps` setting cannot impose `LR_COST` on a disabled server
- [x] 8.4 Require `RNS.Transport.LR_COST` in the client generation capability probe
- [x] 8.5 Re-read the advertised cost on every reconnect by configuring stamping inside `_connect_once`
- [x] 8.6 Validate stamp flags before the `identity` subcommand early return; an explicit client cost `0` disables native stamping
- [x] 8.7 Add unit coverage for `StampedDestination` (drop before `super()`, release on no append/exception, timeout clamp, early-init drop) and for the replay cache under threads
- [x] 8.8 Add unit coverage for the client fail-fast path, cost `0`/missing app data, and `make_in_destination` pinning cost `0`
- [x] 8.9 Make rejection integration tests deterministic with a cost-20 server, keeping cost 8 for success tests
- [x] 8.10 Update README/AGENTS with reconnect cost re-reading, `enforce_stamps` handling, client cost `0` semantics, watchdog granularity and `ps` visibility

