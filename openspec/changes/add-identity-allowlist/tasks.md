## 1. Configuration and data model

- [x] 1.1 Add `allowed_identities: frozenset[bytes] = frozenset()` to `Service`, plus an `is_restricted` property (`password is not None or bool(allowed_identities)`), and make `Service.public()` return `"auth": self.is_restricted`; run `tests/unit/test_registry.py` and `tests/unit/test_config.py` to confirm existing constructions still work
- [x] 1.2 Add `parse_identity_hash(value)` (32 hex characters, case-insensitive, returns 16 bytes) and `parse_allow_identity(value)` (`SERVICE:HASH` → name + hash, reusing `validate_name`) to `config.py`; every malformed input raises `ConfigError` naming the 32-hex expectation, and unit-test valid, uppercase, short, long, non-hex, missing separator and empty name
- [x] 1.3 Add `apply_allow_identities(services, entries)` using `dataclasses.replace` to accumulate one or more hashes per service; reject unknown service names with `ConfigError`; unit-test multiple entries per service, duplicates, unknown name, and that services without entries are unchanged
- [x] 1.4 Preserve `allowed_identities` in `apply_server_secrets`' explicit `Service(...)` rebuilds and unit-test a `-` password service that also has an allow-list
- [x] 1.5 Add `IDENTITY_WAIT_TIMEOUT = 5.0` to `constants.py` and note it in the module docstrings/comments where the server uses it

## 2. Registry and server authorization

- [x] 2.1 Change `ServiceRegistry.authenticate()` and `probe()` to accept the caller's identity hash and require both the password rule and the allow-list rule; a public service keeps ignoring the password but enforces its allow-list, and `identity_hash=None` matches only services with no allow-list. Unit-test: allowed+captcha, allowed+wrong password, unlisted+correct password, public+allowed, public+unlisted, unknown service still using `dummy_compare`
- [x] 2.2 Select the connection limit with `not service.is_restricted` in `_limit_for()` and unit-test that a public service with an allow-list stops at `--max-connections-per-service` rather than the public limit
- [x] 2.3 Add `identity_hash`, `pending_opens` and `identity_timer` to `LinkContext`; register `set_remote_identified_callback` in `_link_established`; implement `_remote_identified` to store the hash, cancel the timer and authorize pending requests through the normal completion path
- [x] 2.4 Refactor `_handle_open` into the immediate path plus a pending path: restricted service with unknown identity reserves the stream id, records the message and lazily starts one `IDENTITY_WAIT_TIMEOUT` timer per Link; keep unknown-service, version, `LIMIT` and reservation behavior unchanged on the immediate path
- [x] 2.5 Implement the timer expiry path: reject every pending `OPEN` through the existing delayed `AUTH` helper (`record_failure`, delayed `OPEN_ERR`, teardown on threshold) and release each stream reservation; unit-test an `OPEN` sent before the identify callback, an identify that arrives in time, and an expiry with the delayed error plus counter increment
- [x] 2.6 Cancel the identity timer and drop pending opens on `_link_closed` and `stop()` without double-releasing service slots; unit-test link close with a pending request
- [x] 2.7 Pass `ctx.link.get_remote_identity()` through to `authenticate` in `_handle_open`, covering TCP and UDP opens with the same handler; unit-test that an unlisted identity never starts `_open_backend`
- [x] 2.8 Pass the request `remote_identity` hash to `/probe` and unit-test allowed success, unlisted rejection with the same `{"ok": false, "error": "auth"}` shape and delay as a wrong password, and that `/list` keeps returning every service for an unlisted caller

## 3. Server CLI

- [x] 3.1 Add repeatable `--allow-identity SERVICE:HASH` (metavar `SERVICE:HASH`) to `cli_server.py`, apply it after secret resolution, and keep the stdout `services:` line unchanged; unit-test argparse accumulation and that unknown service or bad hash exits with the configuration code before startup
- [x] 3.2 Update the server `--help` examples and text for `--allow-identity`, and verify `announce_data()` and startup logs contain no allow-listed hashes or service-to-identity mapping
- [x] 3.3 Confirm `--allow-identity` composes with `--expose`, `--expose-udp`, `--expose-public`, `--expose-udp-public`, `--list-password` and both secret sources; unit-test one mixed configuration end to end through `main` argument handling without starting Reticulum

## 4. Client identity

- [x] 4.1 Add `--identity PATH` to the client global parser and the common subcommand parent so it works before or after `forward`/`list`/`probe`; without the flag the value stays `None` and no identity is loaded
- [x] 4.2 Extend `LinkManager` with optional `client_identity` and `logger`; call `link.identify(client_identity)` from `_on_established` inside a guarded block that logs failures without aborting; unit-test with a fake link that identify runs on establishment, runs once per manager, and is skipped when no identity is configured
- [x] 4.3 Load the identity once in `cli_client.main` with `load_or_create_identity` (existing `0700`/`0600` behavior) and pass it to `PortfwdClient`, `run_list` and `run_probe`; ensure every reconnect uses the same identity object and that omitted `--identity` leaves the existing anonymous paths untouched
- [x] 4.4 Add the `identity` subcommand with `--identity` defaulting to `~/.config/rns-portfwd/client.id`; it SHALL print only the 32-hex hash on stdout, exit 0, create the file when missing, and not initialize Reticulum; unit-test output format, creation, default path and use of an existing file
- [x] 4.5 Update the client `--help`/examples for `--identity` and `identity`, and unit-test that a `probe`/`list`/`forward` invocation with `--identity` forwards the identity to the Link layer while an anonymous invocation does not

## 5. Integration

- [x] 5.1 Extend `tests/integration/harness.py` with helpers to create per-client identity files, pass `--identity` to clients, and pass `--allow-identity` to the server, reusing the isolated `rnsd`
- [x] 5.2 Cover a TCP forward with an allow-listed identity end to end: server allow-lists the client hash, client uses `--identity`, echo bytes match
- [x] 5.3 Cover rejection end to end: a second client identity and an anonymous client both fail to open the allow-listed service (probe exits 5, forward reports auth failure), while a service without an allow-list keeps working for them
- [x] 5.4 Cover an allow-listed public service: allowed identity connects with an empty password, unlisted identity is rejected, and `/list` reports the entry with `auth: true`
- [x] 5.5 Cover an allow-listed UDP service: allowed identity exchanges datagrams, unlisted identity is refused before any backend socket is opened
- [x] 5.6 Confirm no regression for existing anonymous clients against servers without allow-lists (run the existing integration suite)

## 6. Docs and verification

- [x] 6.1 Update `README.md`: `--allow-identity` syntax, `--identity` client usage, the `identity` subcommand, the password-AND-identity rule for private services, public services with allow-lists (`auth: true`, per-service limit), the 5-second identity window, security notes (identity hashes are public, `ps` exposure, `--identity` privacy trade-off, client lockout if it does not identify) and the limitations list
- [x] 6.2 Update `AGENTS.md` protocol invariants: allow-list enforcement on `OPEN`/`/probe`/UDP, indistinguishable rejection, `auth` semantics, `--max-connections-per-service` for restricted public services, no hashes in announces, client identifies before application traffic
- [x] 6.3 Run `python3 -m pytest tests/unit -q`, then the full `python3 -m pytest -q`, and `ruff check src tests`; all must pass with the new tests included
- [x] 6.4 Run `openspec validate add-identity-allowlist --strict` and fix any spec formatting issue before reporting the change as ready

## 7. Review follow-ups

- [x] 7.1 Defer the protocol-version error of an allow-listed service until after identity authorization, so an anonymous client cannot use it as an existence/allow-list oracle or skip failure accounting
- [x] 7.2 Retry `/probe` once after re-sending `Link.identify()` when an identified client receives the generic auth failure, covering a lost or reordered identify packet; improve the probe error message to mention `--identity`
- [x] 7.3 Guard each pending completion/rejection against unexpected exceptions and release its reservation, so one failure cannot strand the rest of the pending opens
- [x] 7.4 Reject `--auth-timeout` values at or below `IDENTITY_WAIT_TIMEOUT` when `--allow-identity` is used
- [x] 7.5 Parse and validate `--allow-identity` names and hashes before reading a secret source
- [x] 7.6 Reject an empty `--identity`, load the identity only after the subcommand configuration is validated, and log the client hash at `debug`
- [x] 7.7 Create a missing identity file with owner-only permissions before writing the key, and tighten lax permissions on existing identity files
- [x] 7.8 Update the spec/design wording for the per-Link wait window and probe retry, and extend unit/integration tests for the review fixes
