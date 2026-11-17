## Context

See `proposal.md` for motivation and `specs/` for the behavior contract.

Current authorization lives in `ServiceRegistry.authenticate()`: it looks up the
service, compares the password in constant time (public services skip it), and
`PortfwdServer._handle_open()` maps a `None` result to a delayed `OPEN_ERR` plus
`AuthTracker` accounting. `/probe` uses the same registry method; `/list`
returns `Service.public()`.

Two Reticulum constraints shape the design:

- A `Link` initiator is anonymous unless it calls `link.identify(identity)`
  after the link is `ACTIVE`; the responder then receives the identity through
  `set_remote_identified_callback`. Without it, `link.get_remote_identity()`
  is `None`. Links created from an `RNS.Destination` use an ephemeral identity,
  so identity allow-lists cannot work without a client change.
- The identify proof and the first `OPEN` travel as separate packets on the
  same Link. The server may process the `OPEN` callback before the identify
  callback, so authorization has to tolerate a short identity-arrival window.

## Goals / Non-Goals

**Goals:**

- Per-service allow-list validated on `OPEN` (TCP and UDP), `/probe`, in
  addition to the password rule, without changing behavior for services that
  have no allow-list.
- Rejections indistinguishable from wrong-password rejections.
- Opt-in client identity that stays anonymous by default.

**Non-Goals:**

- Filtering `/list` per caller or hiding service names.
- Allowing identities by display name, path, or public key instead of hash.
- A server-side identity database, revocation list, or enrollment protocol.
- Changing announces or the wire `OPEN`/`DATA`/`DGRAM` layouts.
- Making allow-lists secret; identity hashes are public values and are accepted
  on the command line.

## Decisions

### Allow-list lives on `Service` as a set of 16-byte hashes

`Service` gains `allowed_identities: frozenset[bytes] = frozenset()` and a
derived `is_restricted` property (`password is not None or
bool(allowed_identities)`). Hashes are parsed from 32-hex text with a new
`parse_identity_hash()` that raises `ConfigError` (exit code 2), not
`ResolutionError`, because this is command-line configuration. `Service.public()`
returns `"auth": self.is_restricted`, which keeps `auth` meaning "access is
controlled".

Configuration flow changes in `cli_server.main`: parse and validate services,
resolve secrets, then apply `--allow-identity` with
`dataclasses.replace()` so frozen services are rebuilt. `apply_server_secrets`
also copies `allowed_identities` through its explicit `Service(...)` rebuilds,
so the order cannot silently drop the field. Name validation happens after all
`--expose*` flags are parsed; unknown names and malformed hashes abort before
the server starts.

Alternative considered: a JSON `"allow"` map in the secret document. Rejected:
allow-listed hashes are not secrets, it forces a secret source onto public
services, and it adds cross-validation between two config surfaces.

### Registry takes the caller identity explicitly

`ServiceRegistry.authenticate(name, password, identity_hash)` and
`probe(name, password, identity_hash)` check the service exists, then evaluate
the password rule and the allow-list rule; both must pass. Unknown service
keeps `dummy_compare()` and returns `None`. A public service still ignores
the password value but checks the allow-list when present. `None` identity only
matches services without an allow-list.

`_limit_for()` switches from `password is None` to `not service.is_restricted`,
which gives allow-listed public services the per-service limit decided in the
specs.

Alternative considered: separate `authorize_identity()` call after password
authentication. Rejected: two entry points invite paths that forget one; a
single method keeps `OPEN` and `/probe` consistent.

### Server waits for identification without blocking the Channel thread

`LinkContext` gains `identity_hash: bytes | None`, `pending_opens:
dict[int, OpenMessage]`, and `identity_timer`.

- `_link_established` registers
  `link.set_remote_identified_callback(self._remote_identified)`.
- `_handle_open` keeps the existing unknown-service handling and defers only
  allow-listed services whose identity is not yet known; the version error is
  produced in the completion path, after authorization, so an anonymous client
  cannot use a version mismatch as an oracle for "service exists and has an
  allow-list".
  - if the service is not restricted or the identity is already known, run the
    current authorization path immediately;
  - otherwise reserve the stream id (bounds concurrent pending requests to
    `--max-streams`), store the message in `ctx.pending_opens`, and start one
    `threading.Timer(IDENTITY_WAIT_TIMEOUT, ...)` per Link on first use.
- `_remote_identified` stores the hash, cancels the timer, and authorizes every
  pending `OPEN` through the same completion routine used by the immediate path
  (password check, `tracker`, `acquire`, backend thread).
- The timer callback rejects every pending `OPEN` with the existing delayed
  `AUTH` error path (`record_failure`, delayed `OPEN_ERR`, teardown on
  threshold), releasing each reservation.
- `_link_closed` and `stop()` cancel the timer and let `Mux.close_all()` drop
  pending reservations; waiting state checks `ctx.closing`.

`IDENTITY_WAIT_TIMEOUT = 5.0` in `constants.py`. This is deliberately not a CLI
flag: it is a protocol race tolerance, not an operator tuning knob.

Alternative considered: blocking `_handle_open` on an `Event`. Rejected: the
Channel callback is serialized per Link, so one anonymous `OPEN` would stall
every other stream for five seconds. Alternative considered: a thread per
pending `OPEN`. Rejected in favor of a single per-Link timer plus the pending
map, which is bounded by `--max-streams`.

### Client identity is opt-in and presented before application traffic

`LinkManager` gains an optional `client_identity` and a `logger`. In
`_on_established` (the Link is `ACTIVE` there), it calls
`link.identify(client_identity)` inside a `try/except` that logs failures but
does not fail the connection; an identification failure surfaces later as an
`AUTH` error from the server. The same identity is reused on every reconnect
because `PortfwdClient._connect_once` builds a new `LinkManager` with the stored
identity.

`cli_client.py` loads the identity with the existing
`load_or_create_identity()` (creates parent directories `0700` and the file
`0600`), passes it to `PortfwdClient`, `run_list`, and `run_probe`, and skips
loading entirely when `--identity` is absent. The client hash is logged only at
`debug`, and private key material is never logged.

Because `Link.identify()` is a single packet that Reticulum does not
retransmit, the startup `/probe` retries once after re-identifying when an
identified client receives the generic auth failure. Stream `OPEN`s do not need
this retry because the server holds them in the identity wait window.

The new `identity` subcommand only calls `load_or_create_identity()` and prints
`identity.hash.hex()`; it does not initialize Reticulum, so it works without
`rnsd`.

Alternative considered: a default client identity plus `--anonymous`.
Rejected: silently identifying every existing client changes its privacy
posture on every server it already uses.

### Rejection behavior reuses the password failure path

`_handle_open` already centralizes identity/password failure in a delayed
`AUTH` response with failure accounting. Allow-list rejection calls the same
helper, so timing, error code, log level (service name and attempt count only,
identity at `debug`), and teardown threshold stay identical. `/probe` appends
the allow-list check to the existing uniform failure response and delay; it
keeps the current behavior of not touching `AuthTracker` for requests.

## Risks / Trade-offs

- **Identify packet loss** → the client keeps working for servers without
  allow-lists; against allow-listed services the `OPEN` fails after the wait
  window and the client's normal reconnect/probe error path reports an auth
  failure. Mitigation: the wait window absorbs the common OPEN race, and the
  client's `/probe` re-identifies and retries once.
- **Identification race at Link establishment** → resolved by the pending-open
  map and timer; tests must send `OPEN` before the identify callback.
- **Anonymous clients lock themselves out** → documented as a breaking
  behavioral change for allow-listed services only; the error is the generic
  auth failure so operators must check client flags. The `identity` subcommand
  exists to make provisioning explicit.
- **Hash typo locks out legitimate users** → impossible to detect
  semantically; mitigated by strict 32-hex validation and an error naming the
  service. Operators should keep the identity file safe and record its hash.
- **Privacy**: `--identity` reveals a stable identity to every server the
  client connects to, and `--allow-identity` exposes hashes in the process
  list. Mitigation: opt-in flag, documented in the README.
- **Pending-open DoS** → pending requests are bounded by `--max-streams` and
  each Link has at most one timer; anonymous clients can at worst hold their
  own stream slots for five seconds.
- **`/list` remains unfiltered** → a caller with the list password learns
  allow-listed service names and `auth: true`. Accepted: it already learns
  service names; identity values are never listed.
- **Time-of-check semantics** → identity is checked at `OPEN` time. An
  application-level stream opened while allowed keeps running even if the
  operator restarts the server with a different allow-list; this matches
  password behavior and requires reconnection to re-evaluate.

## Migration Plan

1. Ship the server change and document `--allow-identity`. Existing
   configurations are unchanged because the flag is additive.
2. Provision client identities: run `rns-portfwd-client identity --identity
   PATH`, collect hashes, add `--allow-identity` entries.
3. Roll clients over to `--identity PATH` before enabling an allow-list on a
   service; otherwise those clients receive auth failures.
4. Rollback: remove the `--allow-identity` flag server-side (clients may keep
   identifying harmlessly) or drop `--identity` client-side (allow-listed
   services then reject them).

No data migration is required; no stored format changes.

## Open Questions

None.
