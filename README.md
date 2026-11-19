# rns-portfwd

Multiplexed TCP and UDP port forwarding over [Reticulum](https://reticulum.network/).

`rns-portfwd-server` exposes named TCP and UDP services from a machine
reachable over a Reticulum mesh. `rns-portfwd-client` resolves that server by
destination hash, opens one `Link`, and maps every local TCP connection or UDP
flow to a stream over that `Link`. It is similar in spirit to `ssh -L`, but over
Reticulum instead of IP.

## Features

- Named TCP and UDP services with an independent password per service.
- Optional passwordless public services (`--expose-public`,
  `--expose-udp-public`) with a separate, lower concurrency limit
  (`--max-connections-per-public`, 8 by default).
- Clients forward to public services with `--forward-public` or
  `--forward-udp-public`; `probe` works without `--service-password`.
- Per-service identity allow-lists (`--allow-identity`) that restrict access by
  Reticulum identity hash, independently of the service password. Clients
  present a persistent identity with `--identity`; anonymous clients remain the
  default.
- Opt-in link-request stamping (`--link-stamp-cost`) that makes opening a
  `Link` cost client CPU, plus caps for links stuck before the handshake
  completes.
- One Reticulum `Link` per server, multiplexing all forwards and connections.
- Per-stream byte credits, so a slow consumer cannot block the other streams.
- Half-close and byte-for-byte fidelity for TCP; datagram boundaries for UDP.
- UDP listeners map each local source address to its own session (NAT-like).
- Optional per-payload bz2 compression for TCP.
- Stealth by default: no announces unless `--announce` is used; clients address
  servers by destination hash.
- Private enumeration: `/list` is disabled unless `--list-password` is set.
- No application configuration files. Secrets can be passed inline, via stdin,
  or via a file descriptor.

## Requirements

- Python 3.10 or newer.
- Reticulum (`rns>=1.5.7,<2`). Link-request stamping needs Reticulum 1.6; the
  rest of the features work on 1.5.7.
- A running `rnsd` (or any Reticulum process) that provides a shared instance.

## Install

```bash
python -m pip install .
# or, for development:
python -m pip install -e .
```

This installs the `rns-portfwd-server` and `rns-portfwd-client` commands.

## Quick start

### 1. Start Reticulum with a shared instance

The default Reticulum configuration normally has `share_instance = Yes`:

```bash
rnsd
```

If you use a custom configuration directory, pass it to both applications with
`--config /path/to/reticulum-config`.

### 2. Start the server

```bash
rns-portfwd-server \
  --expose 127.0.0.1:22:ssh:'correct horse battery staple' \
  --expose-udp 127.0.0.1:5353:dns:dns-password \
  --expose 127.0.0.1:5432:db:db-password \
  --list-password list-password \
  --name my-server
```

`--expose` accepts `host:port:name:password` for TCP and `--expose-udp` accepts
the same format for UDP. The password may contain `:` because it consumes the
rest of the field. IPv6 hosts must be bracketed:

```bash
rns-portfwd-server --expose '[::1]:5432:db:secret'
rns-portfwd-server --expose-udp '[::1]:5353:dns:secret'
```

Service names must be unique across TCP and UDP. Use `--expose-public` or
`--expose-udp-public` for a service with no password; see
[step 7](#7-expose-and-forward-a-service-without-a-password).

At startup the server prints its destination hash:

```
destination: 8ed9308efb95ff5bc8a5253ab9789116
services: ssh, dns, db
```

Share that hash and the private service passwords out of band. Public services
need no password. The default identity file is
`~/.config/rns-portfwd/server.id`; keep it to keep the same address. Use
`--identity` to choose another path.

### 3. List services (optional)

Enumeration requires the server's `--list-password`:

```bash
rns-portfwd-client list \
  --server 8ed9308efb95ff5bc8a5253ab9789116 \
  --list-password list-password
```

### 4. Probe a service (optional)

```bash
rns-portfwd-client probe \
  --server 8ed9308efb95ff5bc8a5253ab9789116 \
  --service ssh \
  --service-password 'correct horse battery staple'
```

### 5. Forward a local port

```bash
rns-portfwd-client forward \
  --server 8ed9308efb95ff5bc8a5253ab9789116 \
  --forward ssh:2222:'correct horse battery staple'
```

Then connect locally:

```bash
ssh -p 2222 user@127.0.0.1
```

Several forwards share one Link:

```bash
rns-portfwd-client forward \
  --server 8ed9308efb95ff5bc8a5253ab9789116 \
  --forward ssh:2222:ssh-password \
  --forward-udp dns:5353:dns-password \
  --forward db:15432:db-password
```

Use `--forward name:0:password` to request an ephemeral local port; the client
prints the port it obtained. The default bind address is `127.0.0.1`; change it
with `--bind`.

### 6. Forward a local UDP port

```bash
rns-portfwd-client forward \
  --server 8ed9308efb95ff5bc8a5253ab9789116 \
  --forward-udp dns:5353:dns-password
```

Then send datagrams locally:

```bash
dig @127.0.0.1 -p 5353 example.com
```

The local UDP listener is NAT-like: each source address and port gets its own
remote session and its own backend socket, so several local processes can use
the same forward. Replies go only to the source that sent the datagram. A
session closes after `--udp-idle-timeout` seconds without traffic (60 by
default); a later datagram from the same source creates a new session.

Each datagram must fit in one Channel message, which is also capped by the
peer's credit window (`--window`, 16 KiB by default). Larger datagrams are
dropped and logged: there is no application-level fragmentation. On a fast
Reticulum link the limit is several kilobytes; on LoRa it is a few hundred
bytes. UDP datagrams are carried reliably and in order by the Channel, which is
stricter than IP UDP; there is no loss, duplication or reordering to recover
from. UDP has no half-close or resume: sessions close on idle timeout or Link
loss, and new datagrams open new sessions after reconnection.

### 7. Expose and forward a service without a password

`--expose-public` and `--expose-udp-public` declare a TCP or UDP service that
accepts any password, including an empty one. They take `host:port:name` with no
password field (IPv6 hosts stay bracketed). Public and password-protected
services can be mixed on one server:

```bash
rns-portfwd-server \
  --expose 127.0.0.1:22:ssh:ssh-password \
  --expose-public 127.0.0.1:80:web \
  --max-connections-per-public 4 \
  --list-password list-password
```

`--max-connections-per-public` (8 by default) bounds concurrent connections per
public service. It is independent of `--max-connections-per-service` (16 by
default), which still applies to password-protected services.

A client forwards a public service without a password:

```bash
rns-portfwd-client forward \
  --server 8ed9308efb95ff5bc8a5253ab9789116 \
  --forward-public web:8080
```

`--forward-udp-public name:local_port` does the same for UDP. `probe` also works
without `--service-password`; it sends an empty password, so it only succeeds
against a public service:

```bash
rns-portfwd-client probe \
  --server 8ed9308efb95ff5bc8a5253ab9789116 \
  --service web
```

`list` shows a public service as `AUTH no`, and `probe` prints `auth=no` for it;
password-protected services show `yes`:

```
SERVICE                  PROTOCOL   AUTH
web                      tcp        no
ssh                      tcp        yes
```

`auth` answers only "does this service require authorization?", never the
password itself. If `probe` is run without `--service-password` against a
service that requires one, it exits with code 5 and suggests
`--service-password`. Anyone who knows the server's destination hash can use an
unrestricted public service; see [Security notes](#security-notes).

### 8. Restrict a service to specific identities

`--allow-identity SERVICE:HASH` adds a Reticulum identity hash to a service's
allow-list. Repeat the flag to allow several identities on the same service.
The hash is the 32-character hexadecimal identity hash, not a display name:

```bash
rns-portfwd-server \
  --expose 127.0.0.1:22:ssh:ssh-password \
  --allow-identity ssh:0123456789abcdef0123456789abcdef \
  --allow-identity ssh:fedcba9876543210fedcba9876543210
```

A client creates its persistent identity once and prints its hash:

```bash
rns-portfwd-client identity --identity ~/.config/rns-portfwd/client.id
```

The command prints only the hash. Without `--identity` it uses
`~/.config/rns-portfwd/client.id`, which is created on first use. Share the
hash with the server operator, then use the same file on every client command:

```bash
rns-portfwd-client forward \
  --server 8ed9308efb95ff5bc8a5253ab9789116 \
  --forward ssh:2222:ssh-password \
  --identity ~/.config/rns-portfwd/client.id
```

`--identity` is also accepted by `list` and `probe`, before or after the
subcommand. Without it the client stays anonymous, exactly as before. Against a
service with an allow-list, an anonymous client is rejected no matter which
password it sends.

The allow-list is an **additional** requirement, not a replacement:

- A password-protected service requires both the password and an allowed
  identity.
- A public service (`--expose-public` / `--expose-udp-public`) still ignores the
  password, but requires an allowed identity.
- A wrong password or an unlisted identity receives the same authentication
  error, delay, and failure accounting; an anonymous client is rejected after
  the identity wait window (5 seconds plus the usual delay). Repeated failures
  close the Link as before.

Identity proofs travel over Reticulum's own `Link` identification and are
checked before the backend is opened. An `OPEN` that races the proof is held
for up to 5 seconds, so a client that identifies immediately after connecting
works normally.

`/probe` enforces the allow-list too. `/list` still lists every service, but a
service with a password or an allow-list shows `AUTH yes`; `probe` prints
`auth=yes` for it:

```
SERVICE                  PROTOCOL   AUTH
web                      tcp        yes
ssh                      tcp        yes
```

A public service that has an allow-list is no longer unrestricted, so it uses
`--max-connections-per-service` instead of `--max-connections-per-public`.
Announces never reveal allow-lists or identity hashes.

An unknown service in `--allow-identity`, or a hash that is not exactly 32
hexadecimal characters, exits with the configuration code (2) before the server
starts.

### 9. Require proof-of-work to open a Link

Reticulum 1.6 can embed a proof-of-work stamp in the link request itself, before
the server creates the `Link`, signs a proof, or allocates any per-link state.
`rns-portfwd-server` can require it, and `rns-portfwd-client` applies it
automatically:

```bash
rns-portfwd-server \
  --expose 127.0.0.1:22:ssh:ssh-password \
  --link-stamp-cost 12.5 \
  --max-half-open 64 \
  --half-open-timeout 60
```

- `--link-stamp-cost` is the minimum stamp value. `0` (the default) disables
  stamping and keeps the previous behavior. The valid range is `0`–`40`.
- `--max-half-open` bounds links in `PENDING`/`HANDSHAKE`, which are not counted
  by `--max-links`; the default is 64 and `0` means unlimited.
- `--half-open-timeout` clamps how long an accepted but incomplete Link may
  live; the default is 60 seconds, well below Reticulum's multi-minute default.
  Reticulum's watchdog sleeps up to 5 seconds per check, so smaller values are
  approximate.

When the cost is positive, the server advertises `stamp_cost` in its announce
app data, including path responses, so clients discover it even when the server
does not announce periodically. A request with a missing, insufficient, or
replayed stamp is dropped silently: no `Link`, no proof, no auth error, and no
`OPEN` handler. Accepted `(ephemeral key, signing key)` pairs are remembered for
10 minutes so a precomputed stamp cannot be reused immediately.

The client reads `stamp_cost` after resolving the server and enables Reticulum
link stamping before opening the Link. It re-reads the advertised cost on every
reconnect, so enabling or changing the cost server-side does not require
restarting connected clients. Two overrides exist:

```bash
# Force a cost against a stealth server that hides the field
rns-portfwd-client forward --server <hash> --forward ssh:2222:pw \
  --link-stamp-cost 12.5

# Diagnose or intentionally connect without stamping
rns-portfwd-client list --server <hash> --list-password lp --no-link-stamp
```

`--link-stamp-cost` and `--no-link-stamp` are mutually exclusive. An explicit
client cost of `0` also disables native link stamping, including a cost learned
from Reticulum's own destination stamps; `--no-link-stamp` is the equivalent
diagnostic flag. A client whose Reticulum cannot generate stamps (RNS 1.5.x)
fails with exit code 4 before opening a Link when the server requires them.

The server pins the destination's native link cost, including `0`, so a
Reticulum-wide `[stamps] enforce_stamps = yes` setting does not impose an
unrelated `LR_COST` on a server that disabled stamping. Costs and hashes are
public values and may appear in `ps`; they are not secrets.

Compatibility:

| Server cost | Client | Result |
|---|---|---|
| `0` | Any | Unchanged |
| `> 0` | `rns-portfwd-client` with `rns>=1.6` | Automatic stamping |
| `> 0` | Client with `--link-stamp-cost` at least the server cost | Works, even without the advertised field |
| `> 0` | `--no-link-stamp`, a lower cost, RNS 1.5.x, or a generic RNS client that does not stamp | No Link; establishment timeout |
| `> 0` | Allow-listed identity that does not stamp | No Link; allow-lists do not exempt stamps |

Cost guidance: each point roughly doubles the work. `12.5` is Reticulum's
default and a good interop point; `14`–`16` makes abuse meaningfully expensive
but can approach Reticulum's 15-second generation timeout on slow hardware. The
default is disabled, so deploy servers and clients before enabling it. Stamping
protects Link establishment; it does not replace passwords, allow-lists, or the
per-service limits.


## Secrets without exposing them in `argv`

Passwords passed inline are visible to other local users through `ps` and shell
history. For every secret that should come from elsewhere, use `-` as the
placeholder and supply a JSON document on stdin or on a file descriptor:

```bash
printf '%s' '{"list_password": "lp", "services": {"ssh": "sp", "db": "dp"}}' |
  rns-portfwd-server \
    --expose 127.0.0.1:22:ssh:- \
    --expose 127.0.0.1:5432:db:- \
    --list-password - \
    --password-stdin
```

The same JSON schema is used with `--secrets-fd <fd>`. For a client:

```bash
printf '%s' '{"services": {"ssh": "sp", "db": "dp"}}' |
  rns-portfwd-client forward \
    --server 8ed9308efb95ff5bc8a5253ab9789116 \
    --forward ssh:2222:- \
    --forward db:15432:- \
    --password-stdin
```

For `list` the document is `{"list_password": "..."}`. For `probe` use
`{"services": {"<name>": "<password>"}}`. UDP services and forwards use the same
document and key by service name.

Public services are exempt from secret sources: `--expose-public`,
`--expose-udp-public`, `--forward-public` and `--forward-udp-public` carry no
password, so they need neither the `-` placeholder nor an entry in the
document. They can be mixed with private services that do use a secret source:

```bash
printf '%s' '{"services": {"ssh": "sp"}}' |
  rns-portfwd-server \
    --expose 127.0.0.1:22:ssh:- \
    --expose-public 127.0.0.1:80:web \
    --password-stdin
```

On the client, `--forward-public web:8080` can sit next to
`--forward ssh:2222:-` in the same invocation. Inline and external secrets
cannot be mixed: when a secret source is used, every private password must be
`-`, while public services remain exempt. Logs and error messages redact
registered secrets.

## Discovery and announces

By default the server is stealthy: it does not announce, but it answers path
requests for its destination, so a client that knows the hash can reach it.
`--announce` emits a minimal announce at startup and every
`--announce-interval` seconds (900 by default). The announce carries only a
protocol version, the display name, whether enumeration is enabled, and — when
link stamping is enabled — the advertised stamp cost. It never contains service
names, backend addresses, identity hashes, or which services are public or
allow-listed.

Always address servers by destination hash. Display names are labels, not
credentials and not addresses.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Clean shutdown |
| 1 | Runtime or protocol error |
| 2 | Invalid command-line configuration |
| 3 | Reticulum shared instance or identity file unavailable |
| 4 | Server hash/path could not be resolved |
| 5 | Authentication or probe failure |
| 6 | Local bind failure |

`--help` exits with 0.

## Logging and diagnostics

Both commands write diagnostics to `stderr`. `-v` / `--verbose` is repeatable
and may appear before or after a subcommand:

| Flag | Application log | Reticulum log |
|------|-----------------|---------------|
| (none) | `INFO` | `CRITICAL` |
| `-v` | `DEBUG` | `NOTICE` |
| `-vv` | `DEBUG` | `DEBUG` |

Application `DEBUG` is the diagnostic level: it includes link ids, identity
hashes and identity-wait decisions, which are never logged below `debug`.
Registered secrets are redacted at every level, including `DEBUG`.

Reticulum's logger is independent. `-vv` enables its debug output, which is very
noisy and is not filtered through the application's redaction; no application
secret is passed to Reticulum's logger.

Repeated UDP drops are reported as periodic `DEBUG` summaries with counts per
reason instead of one line per datagram, and pending counts are flushed when the
session closes.

## Security notes

- Passwords travel inside the end-to-end encrypted Reticulum `Link`; they are
  not sent as plain packets.
- Authentication failures for an unknown service and a wrong password are
  indistinguishable. Per-Link attempts are limited, and the server closes a
  Link after repeated failures.
- The server never reveals backend host/port or service passwords in responses,
  announces, or errors.
- The client is anonymous at the Reticulum identity layer by default: a private
  service's password is the only credential, and an unrestricted public service
  has no credential at all. With `--identity`, the client identifies itself to
  every server it connects to; the hash is public, but the identity is stable
  and linkable, so enable it only where an allow-list is needed.
- An allow-list is checked in addition to the password and before any backend
  connection. A non-allowed identity receives the same authentication failure
  and delay as a wrong password, anonymous clients cannot use allow-listed
  services, and repeated failures close the Link. Identity hashes are public
  values; `--allow-identity` exposes them in `ps` but never in responses,
  announces, or logs below `debug`.
- A public service without an allow-list is usable by anyone who knows the
  destination hash. There is no identity requirement for it, only the public
  connection limit, the Link and stream caps, and whatever the backend enforces
  itself. Public services default to a lower concurrency limit
  (`--max-connections-per-public`, 8) than password-protected ones; a public
  service with an allow-list uses `--max-connections-per-service` because
  access is no longer unrestricted.
- Announces do not reveal which services are public or allow-listed; a client
  only learns `auth` from `/list` or `/probe`.
- Anyone who knows the destination hash can trigger a path response and
  confirm that a stealth server exists. Announces deliberately reveal the
  display name and, when enabled, the link stamp cost.
- Link-request stamping runs before any `Link` or application state exists and
  drops bad or replayed requests silently. Accepted stamps are cached for 10
  minutes; after that a precomputed stamp could be replayed, so the half-open
  caps remain the backstop for the accepted population.
- Half-open links (handshake started but RTT never received) are bounded by
  `--max-half-open` and expired after `--half-open-timeout` instead of
  Reticulum's multi-minute default. This applies even when stamping is disabled.
- Backend connections time out; stream counts, Link counts, and per-service
  connections are bounded.
- TCP connections are not resumed after a Link drop. Live streams and UDP
  sessions are closed, listeners stay up, and the client reconnects with
  exponential backoff; new connections wait up to `--connect-timeout`.

### LoRa and other slow links

The design minimizes handshakes (one Link per server) and uses per-stream
credits, but it cannot create bandwidth. Reticulum `Channel` reduces its window
to 1 when RTT exceeds about 1.45 s, so throughput on LoRa is roughly
`MDU / RTT`. Compression helps with compressible data; `--no-compress` disables
it. Expect high latency, not high throughput.

## Protocol overview

Messages are `RNS.Channel` application messages in the `0xAC00` range:

| Message | Value | Purpose |
|---------|-------|---------|
| `OPEN` | `0xAC01` | stream id, protocol version, service, password |
| `OPEN_OK` | `0xAC02` | stream id, initial credit |
| `OPEN_ERR` | `0xAC03` | stream id, error code |
| `DATA` | `0xAC04` | stream id, eof/compressed flags, bytes |
| `WINDOW` | `0xAC05` | stream id, additional credit |
| `CLOSE` | `0xAC06` | stream id, reason |
| `DGRAM` | `0xAC07` | stream id, one datagram payload |

`/list` and `/probe` are Reticulum `Link` requests. Identity allow-lists reuse
Reticulum's own Link identification: the initiator calls `Link.identify()` once
the Link is `ACTIVE`, and the server checks the proved identity before it
replies to an allow-listed `OPEN` or `/probe`. There is no identity field in the
application messages and the wire layouts stay unchanged. Link-request stamping
is native as well: the proof-of-work is embedded in the link request keyset, so
it adds no application message and no payload bytes. Stream ids are 14-bit and
are never reused on a Link. Each stream has an independent byte window; the
receiver replenishes credit as its local socket drains. The server advertises
its receive window in `OPEN_OK`; the client advertises the reverse window with a
`WINDOW` immediately after `OPEN_OK`. Compression is chosen per payload and is
signalled with the `compressed` flag; decompressed payloads are size-limited.
`DGRAM` messages carry exactly one UDP datagram, are never compressed, are
never split or coalesced, and are counted against the same per-stream byte
credit as TCP data.

## Development

```bash
python -m pytest            # unit + integration suite
python -m pytest tests/unit # no network
ruff check src tests        # lint
```

The integration tests start a private `rnsd` on loopback with an isolated
configuration directory; they do not touch the system Reticulum instance.
Slow tests (Link drop/reconnect, stream isolation with a slow consumer) are
marked `slow` and run by default; deselect them with `-m "not slow"`.

## Limitations (v1)

- UDP datagrams larger than one Channel message or the peer's credit window are
  dropped; there is no fragmentation.
- UDP has no broadcast or multicast forwarding, and a backend that replies from
  a different address or port than the exposed one is not supported.
- No reverse forwarding.
- One server per client process.
- No TCP or UDP resumption after reconnect.
- Identity allow-lists authorize by Reticulum identity hash only; there is no
  revocation API, group, or enrollment flow, and an anonymous client cannot use
  an allow-listed service. Rotating an identity means editing the server flags
  and restarting it.
- Link stamping requires Reticulum 1.6 on both ends. RNS 1.5.x clients and
  generic RNS clients that do not consume the `stamp_cost` app data cannot
  connect to a server with `--link-stamp-cost > 0`; the default is disabled.
- The replay cache is in-memory, per process, and expires entries after 10
  minutes; a restart forgets which stamps were already used.
- No configuration files; everything comes from flags and optional secret
  sources.
