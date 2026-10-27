# rns-portfwd

Multiplexed TCP port forwarding over [Reticulum](https://reticulum.network/).

`rns-portfwd-server` exposes named TCP services from a machine reachable over a
Reticulum mesh. `rns-portfwd-client` resolves that server by destination hash,
opens one `Link`, and maps every local TCP connection to a stream over that
Link. It is similar in spirit to `ssh -L`, but over Reticulum instead of IP.

## Features

- Named services with an independent password per service.
- One Reticulum `Link` per server, multiplexing all forwards and connections.
- Per-stream byte credits, so a slow consumer cannot block the other streams.
- Half-close and byte-for-byte fidelity in both directions.
- Optional per-payload bz2 compression.
- Stealth by default: no announces unless `--announce` is used; clients address
  servers by destination hash.
- Private enumeration: `/list` is disabled unless `--list-password` is set.
- No application configuration files. Secrets can be passed inline, via stdin,
  or via a file descriptor.

## Requirements

- Python 3.10 or newer.
- Reticulum (`rns>=1.5.7,<2`).
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
  --expose 127.0.0.1:5432:db:db-password \
  --list-password list-password \
  --name my-server
```

`--expose` accepts `host:port:name:password`. The password may contain `:`
because it consumes the rest of the field. IPv6 hosts must be bracketed:

```bash
rns-portfwd-server --expose '[::1]:5432:db:secret'
```

At startup the server prints its destination hash:

```
destination: 8ed9308efb95ff5bc8a5253ab9789116
services: ssh, db
```

Share that hash and the service passwords out of band. The default identity
file is `~/.config/rns-portfwd/server.id`; keep it to keep the same address.
Use `--identity` to choose another path.

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
  --forward db:15432:db-password
```

Use `--forward name:0:password` to request an ephemeral local port; the client
prints the port it obtained. The default bind address is `127.0.0.1`; change it
with `--bind`.

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
`{"services": {"<name>": "<password>"}}`.

Inline and external secrets cannot be mixed: when a secret source is used, all
passwords must be `-`. Logs and error messages redact registered secrets.

## Discovery and announces

By default the server is stealthy: it does not announce, but it answers path
requests for its destination, so a client that knows the hash can reach it.
`--announce` emits a minimal announce at startup and every
`--announce-interval` seconds (900 by default). The announce carries only a
protocol version, the display name, and whether enumeration is enabled; it
never contains service names or backend addresses.

Always address servers by destination hash. Display names are labels, not
credentials and not addresses.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Clean shutdown |
| 1 | Runtime or protocol error |
| 2 | Invalid command-line configuration |
| 3 | Reticulum shared instance unavailable |
| 4 | Server hash/path could not be resolved |
| 5 | Authentication or probe failure |
| 6 | Local bind failure |

`--help` exits with 0.

## Security notes

- Passwords travel inside the end-to-end encrypted Reticulum `Link`; they are
  not sent as plain packets.
- Authentication failures for an unknown service and a wrong password are
  indistinguishable. Per-Link attempts are limited, and the server closes a
  Link after repeated failures.
- The server never reveals backend host/port or service passwords in responses,
  announces, or errors.
- The client is anonymous at the Reticulum identity layer; the service password
  is the only credential. Use long passwords.
- Anyone who knows the destination hash can trigger a path response and
  confirm that a stealth server exists. Announces deliberately reveal the
  display name.
- Backend connections time out; stream counts, Link counts, and per-service
  connections are bounded.
- TCP connections are not resumed after a Link drop. Live streams are closed,
  listeners stay up, and the client reconnects with exponential backoff; new
  connections wait up to `--connect-timeout`.

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

`/list` and `/probe` are Reticulum `Link` requests. Stream ids are 14-bit and
are never reused on a Link. Each stream has an independent byte window; the
receiver replenishes credit as its local socket drains. The server advertises
its receive window in `OPEN_OK`; the client advertises the reverse window with a
`WINDOW` immediately after `OPEN_OK`. Compression is chosen per payload and is
signalled with the `compressed` flag; decompressed payloads are size-limited.

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

- TCP only; no UDP.
- Local forwarding only; no reverse forwarding.
- One server per client process.
- No TCP connection resumption after reconnect.
- No client identity/allow-list authorization beyond per-service passwords.
- No configuration files; everything comes from flags and optional secret
  sources.
