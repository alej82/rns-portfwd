# AGENTS.md

Guidance for coding agents working in this repository.

## Project

`rns-portfwd` is a Python package that forwards TCP and UDP through Reticulum.
The server exports named services, password-protected or public; the client
resolves a 32-hex destination hash, opens one `Link`, and multiplexes every
local TCP connection or UDP flow as a stream over that Link.

- User docs: `README.md`
- Package source: `src/rns_portfwd/`
- Tests: `tests/unit/` (no network) and `tests/integration/` (private `rnsd`)

## Environment

- Python `>=3.10`; the working environment is Python 3.11.14 from pyenv.
- Runtime dependency: `rns>=1.5.7,<2`. Native link-request stamping
  (`--link-stamp-cost > 0`) requires Reticulum 1.6 (`RNS.Stamps`,
  `Destination.set_link_cost`); the server and client must feature-gate it and
  fail with a clear configuration/resolution error on 1.5.x.
- Always use the pyenv `python3` / `python3 -m pytest`.
- Do **not** use `/usr/bin/pytest`: it runs Python 3.14 without `RNS` installed.
- Do not add application config files. Runtime configuration is CLI flags plus
  optional JSON secrets via `--password-stdin` or `--secrets-fd`.
- Do not commit, push, or rewrite history unless the user explicitly asks.

## Commands

Run from the repository root:

```bash
# Install for development (already installed in the working environment)
python3 -m pip install -e . --no-deps --no-build-isolation

# Full suite (unit + integration, currently ~60 s)
python3 -m pytest -q

# Fast/unit-only
python3 -m pytest tests/unit -q
python3 -m pytest -m "not slow" -q

# Lint
ruff check src tests
```

The full suite includes `slow` integration tests (Link drop/reconnect and
slow-consumer isolation) by default. Integration tests launch their own `rnsd`
on loopback with isolated shared-instance ports; they must not touch or require
the system Reticulum daemon.

## Module map

| Module | Responsibility |
|---|---|
| `cli_server.py`, `cli_client.py` | Argument parsing, exit codes, process wiring |
| `config.py` | CLI validation, IPv6 endpoints, secret sources |
| `constants.py`, `protocol.py` | Wire constants and Channel message encode/decode |
| `server.py` | Destination, Link setup, `/list`, `/probe`, service auth, backend opens |
| `client.py` | Resolution, listeners, probes, forwarding, reconnect loop |
| `mux.py` | Per-Link stream table, 14-bit stream-id allocation, limits |
| `streams.py` | `ChannelTransport`, `SessionBase`, `StreamSession`, credits, compression, half-close |
| `datagrams.py` | `DatagramSession`: UDP flows, datagram queues, credit, idle close |
| `credits.py` | Byte-credit accounting and window updates |
| `auth.py` | SHA-256 password comparison and failure tracking |
| `registry.py` | Exposed-service definitions and lookup |
| `discovery.py` | Announce payload encoding and validation; link-request stamp guard, destination and cost discovery |
| `link_manager.py` | Link lifecycle and reconnect/backoff helpers |
| `errors.py`, `logging.py` | Error types, exit codes, secret redaction |

`tests/integration/harness.py` owns the isolated `rnsd`, process wrappers, and
echo/bulk TCP plus UDP echo backends. Reuse it instead of starting a system
daemon.

## Protocol invariants

- Messages are `RNS.Channel` application messages in the `0xAC00` range:
  `OPEN=0xAC01`, `OPEN_OK=0xAC02`, `OPEN_ERR=0xAC03`, `DATA=0xAC04`,
  `WINDOW=0xAC05`, `CLOSE=0xAC06`, `DGRAM=0xAC07`.
- `OPEN` carries the 14-bit `stream_id`; do not drop or reorder it.
- `DGRAM` carries exactly one UDP datagram. Never split, coalesce, truncate or
  compress it. Deliver only whole datagrams and preserve their order within a
  session. Drop datagrams larger than `channel.mdu - 2` or the peer's credit
  window instead of fragmenting them.
- Stream ids are monotonic and never reused on a Link. Exhaustion closes the
  Link and triggers reconnect.
- The server grants client-to-server credit in `OPEN_OK`. The client grants the
  reverse direction with a `WINDOW` immediately after `OPEN_OK`.
- Credits are per stream; a blocked consumer must not block other streams.
  Preserve half-close and byte-for-byte delivery for TCP, and datagram
  boundaries for UDP.
- UDP sessions count toward `--max-streams` and the per-service connection
  limit. Use a bounded per-session queue and drop rather than block the
  listener. Close idle sessions after `--udp-idle-timeout` and release the
  service slot.
- `ChannelTransport._send` retries `CEType.ME_LINK_NOT_READY` for up to 30 s and
  must stop retrying when the mux/link closes.
- Compression is opportunistic per payload (bz2, flag in `DATA`) and capped at
  1 MiB after decompression. Never compress `DGRAM`.
- One Link per server. On Link loss, live TCP streams and UDP sessions close,
  listeners remain, and the client reconnects with 1-60 s exponential backoff
  plus jitter. There is no TCP or UDP resumption.
- A successful `/probe` or `/list` authenticates the Link, so a UDP-only client
  is not torn down by `--auth-timeout`. A stream `OPEN` also authenticates it.
- Servers are stealthy by default. `--announce` reveals only version, display
  name, and whether enumeration is enabled; never service names or backends.
- `/list` requires `--list-password`. `/probe` validates service credentials
  without connecting to the backend. Unknown service and wrong password must be
  indistinguishable.
- `Service.password` and `Forward.password` are `str | None`; `None` marks a
  public service or forward. A service declared with `--expose-public` /
  `--expose-udp-public` authorizes any `OPEN` or `/probe` password, including an
  empty one. For a public service without an identity allow-list that is not an
  authentication failure: do not increment `AuthTracker` or delay an `OPEN_ERR`
  for it. When the public service has an allow-list, the password still does not
  matter, but an anonymous or unlisted identity is a normal auth failure.
- `/list` and `/probe` report an unrestricted public service with `"auth": false`;
  a service with a password or an identity allow-list is `"auth": true`.
  Announces never reveal which services are public or allow-listed; the announce
  `auth` field only reflects whether enumeration is enabled.
- Public services are bounded by `--max-connections-per-public` (8 by default),
  not `--max-connections-per-service`. UDP sessions on a public service count
  against the public limit too. A public service with an identity allow-list is
  restricted, so it uses `--max-connections-per-service` instead.
- Per-service identity allow-lists (`Service.allowed_identities`, canonical
  16-byte Reticulum hashes) are an additional check on `OPEN`, `/probe`, and UDP
  sessions: an allow-listed service authorizes only a Link whose initiator
  identified itself with an allowed hash, and a password-protected service still
  checks the password. A rejection must be indistinguishable from a wrong
  password: same `OPEN_ERR` code, delay, `AuthTracker` accounting, and teardown.
  Anonymous clients are rejected by allow-listed services.
- The server learns the caller identity from Reticulum Link identification, not
  from an application message. An `OPEN` for an allow-listed service that races
  the identification is held for up to `IDENTITY_WAIT_TIMEOUT` (5 s), bounded per
  Link by `--max-streams`, then rejected through the normal auth-failure path.
- The client identifies itself only when `--identity PATH` is given; otherwise it
  stays anonymous. `rns-portfwd-client identity` prints the public hash for the
  allow-list. Hashes are never announced, returned in responses, or logged below
  `debug`.
- Link stamping is native and pre-Link: validate `RNS.Stamps` over
  `destination_hash + signing_public_key` in `StampedDestination.incoming_link_request`
  before RNS creates the `Link`, signs the proof, registers it, or starts the
  watchdog. Never move stamp validation into `OPEN`, `/probe`, or `/list`; that
  does not protect Link establishment.
- A missing, insufficient, malformed, or replayed stamp must be dropped
  silently: no Link, no proof, no `OPEN_ERR`, no `AuthTracker` change, debug log
  only. Accepted `(ephemeral, signing)` key pairs live in a bounded replay cache
  (TTL 10 minutes, 16384 entries, oldest evicted).
- Half-open links (`PENDING`/`HANDSHAKE`) are capped by `--max-half-open` (64,
  `0` unlimited) and their establishment timeout is clamped to
  `--half-open-timeout` (60 s). These limits apply with stamping disabled and on
  RNS 1.5.x.
- `--link-stamp-cost` defaults to `0` and must stay opt-in; cost `> 0` requires
  `rns>=1.6` at startup. The client reads `stamp_cost` from the announce app data
  and sets `ALWAYS_STAMP_LRS`/`LR_COST` before the first Link, re-reading the cost
  on every reconnect, and honoring `--link-stamp-cost` and `--no-link-stamp`.
- `make_in_destination` pins the native destination link cost even at `0`, so an
  `enforce_stamps` Reticulum-wide setting cannot impose `LR_COST` on a server
  that disabled stamping. The client's explicit cost `0` disables both
  `ALWAYS_STAMP_LRS` and `STAMP_LRS`.
- The announce may include `stamp_cost` when stamping is enabled. It must never
  include service names, backend addresses, identity hashes, public/allow-list
  flags, or the required cost when stamping is disabled.
- Public services need no entry in the JSON secret document and do not use the
  `-` placeholder. They may be mixed with private services that do use a secret
  source.
- Never expose backend host/port or passwords in responses, announces, logs, or
  errors. Redact registered secrets.
- Address servers only by destination hash; display names are labels.
- Exit codes: `0` clean, `1` runtime/protocol, `2` config,
  `3` shared instance/identity, `4` resolution/hash, `5` auth/probe, `6` bind.

## Code conventions

- Match existing style: `from __future__ import annotations`, type hints,
  dataclasses where appropriate, standard library first.
- Ruff config: line length 100, rules `E,F,I,UP,B,SIM` (`E501` and `SIM105`
  ignored). Keep imports sorted.
- Prefer small, testable functions. Add or update unit tests for parsing,
  validation, and edge cases; use integration tests only when real Reticulum
  behavior matters.
- Preserve existing public CLI behavior unless the change explicitly alters it;
  update `README.md` when flags, output, or security behavior changes.
- Do not log secrets. Test secret redaction and failure paths when touching auth
  or logging.


