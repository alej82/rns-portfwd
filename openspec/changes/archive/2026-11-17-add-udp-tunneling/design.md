## Context

See `proposal.md` for motivation. The current implementation bridges one TCP
socket to one stream id: `StreamSession` owns a connected TCP socket, `Mux`
routes `OPEN`/`DATA`/`WINDOW`/`CLOSE` messages, and `ChannelTransport` turns
sends into Channel messages. `Service` and `Forward` are frozen dataclasses
without a protocol field, `Service.public()` hardcodes `"tcp"`, and the server
tears a Link down after `--auth-timeout` unless a stream opens.

UDP cannot reuse that model directly: datagrams have boundaries, there is no
EOF or half-close, and one local UDP listener must serve several source
addresses at once. This design adds a parallel session type while keeping the
existing Link, Channel, authentication and credit machinery.

## Goals / Non-Goals

**Goals:**

- Expose and forward UDP with the same service name/password model as TCP.
- Preserve datagram boundaries and per-session ordering.
- Keep one Link per server and per-stream isolation.
- Keep TCP behavior and the existing wire version unchanged for TCP-only peers.
- Fail fast when a forward's protocol does not match the exposed service.

**Non-Goals:**

- Fragmenting or reassembling datagrams larger than one Channel message.
- Emulating UDP loss, duplication or reordering; the Channel is reliable and
  ordered, so the tunnel is strictly more reliable than the local UDP path.
- Broadcast or multicast forwarding, reverse forwarding, or UDP resumption
  after a Link drop.
- Per-datagram compression.
- Multiple backend source ports for one local peer beyond the NAT-like mapping.

## Decisions

### Separate CLI flags: `--expose-udp` and `--forward-udp`

The existing `HOST:PORT:NAME:PASSWORD` and `NAME:LOCAL_PORT:PASSWORD` grammars
stay untouched. A scheme prefix (`udp:host:port:...`) would force changes to
the IPv6 bracket parser and make existing help/examples ambiguous; a `protocol`
field in secret documents would not remove the need to name the type on the
command line. Separate flags are unambiguous, backward compatible and keep the
parser refactor small.

`Service` and `Forward` gain a `protocol: str = "tcp"` field (default keeps
existing constructors and tests valid). `validate_services` keeps names unique
across both protocols because a name selects one backend. `validate_forwards`
rejects duplicate local ports within the same protocol but allows a TCP and a
UDP forward to share a port, since the operating system permits both bindings.

### A dedicated `DGRAM` message, not a `DATA` flag

`DGRAM = 0xAC07` carries `stream_id` (2 bytes) plus exactly one payload. A flag
on `DATA` would overload EOF/credit semantics that do not apply to datagrams
and would make a datagram indistinguishable from a fragmented stream chunk.
The new type is registered on both peers and dispatched by `Mux` to
`on_remote_datagram`. `PROTOCOL_VERSION` stays at 1: a new client sends no
`DGRAM` before `OPEN_OK`, so an old server rejects the `OPEN` first with
`OPEN_ERR`.

### NAT-like client mapping with one session per source

The client binds one UDP socket per forward. Its receive loop learns each
`(source_ip, source_port)`, opens a remote stream for it through the existing
`OPEN`/`OPEN_OK` handshake, and keeps a table from source to session. Replies
are sent with `sendto` on the shared socket to the exact source. This matches
how UDP relays normally behave and lets several local processes use one
forward. The alternative, binding the listener to a single peer, would let one
process monopolise the port; a manual `--peer` mode would remove the NAT-like
convenience.

Each source has its own backend socket on the server, so backend replies are
demultiplexed by the stream id. A connected `SOCK_DGRAM` socket is used on the
server side: it filters replies to the configured backend address and surfaces
ICMP port-unreachable as an error instead of accepting stray packets. The
trade-off is that a backend that replies from a different address or port is
not supported.

### No fragmentation; drop oversized datagrams

The maximum payload is `channel.mdu - 2`, further capped by the peer's credit
window. A larger datagram is dropped and counted/logged. Application-level
fragmentation would need reassembly buffers, timeouts and a second failure mode
for a rare case; the usual UDP payloads (DNS, syslog, metrics, discovery) fit
in a Channel message on fast links, and LoRa users already accept small MDUs.
This is documented as a v1 limit.

### Byte credits, bounded queue and a sender thread per session

The server grants client-to-server credit in `OPEN_OK` and the client grants
the reverse with the initial `WINDOW`, exactly as for TCP. A datagram sender
waits until the full datagram's credit is available (`CreditWindow.take`), so a
datagram is never split. To keep the local receive loop non-blocking, each
session has a bounded queue (default 256 datagrams) and a sender thread; when
the queue is full new datagrams are dropped and counted. The receiver sends a
`WINDOW` update after delivering datagrams to the local socket, reusing the
`WINDOW_UPDATE_DIVISOR` policy. This preserves per-stream isolation: a slow or
stalled peer only fills its own queue.

No compression is applied to `DGRAM`; per-datagram bz2 would add latency and
CPU for no benefit on small payloads.

### Session lifecycle

`DatagramSession` tracks `last_activity` on local receive, remote datagram and
window updates. A reaper (the client listener and the server context) closes
sessions idle for `--udp-idle-timeout` seconds by sending `CLOSE` and releasing
the service slot. A later datagram from the same source creates a new session,
so ids are not reused within a Link. Link loss clears the table without
resending, matching TCP behavior.

To avoid duplicating credit/close bookkeeping, the common lifecycle (stream id,
transport, closed event, credit window, `abort`, `notify_closed`) is extracted
into a `SessionBase` in `streams.py`; `StreamSession` keeps its TCP pumps and
`DatagramSession` adds the datagram pumps. `Mux` stores a `Session` protocol and
dispatches `DGRAM` to datagram sessions. The existing TCP unit tests are the
regression guard for the extraction.

### Protocol validation through `/probe`

`Service.public()` reports `protocol`, so `/list` and `/probe` already have the
information. `PortfwdClient._probe_all` compares the reported protocol with each
forward and raises `AuthError` (exit code 5) before listeners are created. This
avoids putting a protocol byte in `OPEN` and avoids a wire-version bump; a
mismatch is a configuration error caught at startup rather than a silent
backend failure.

### Server authentication timer and link lookup

A UDP-only client opens no stream at startup, so the existing
`--auth-timeout` timer would tear its Link down. The server will index contexts
by Reticulum `link_id` and mark the context authenticated when `/probe` or
`/list` succeeds with valid credentials, cancelling the timer. A stream `OPEN`
continues to do the same. This is not a relaxation: a valid probe or listing
password is the same credential the Link needs, and failed attempts still keep
the timer and failure tracking.

### Server backend creation

`_open_backend` branches on `service.protocol`. The UDP path resolves the
backend with `getaddrinfo`, creates a connected datagram socket, verifies the
context is still open, installs the session, sends `OPEN_OK` and starts the
local-to-channel receiver. A resolution/connect error maps to `BACKEND`; a
later `ECONNREFUSED`/`EHOSTUNREACH` closes only that session. The existing
per-service acquire/release and max-stream limits apply unchanged.

## Risks / Trade-offs

- **Datagrams larger than the link MDU are lost** → documented limit, drop is
  logged and counted, protocol reports the effective maximum in errors.
- **Reliable ordered Channel changes UDP application behavior** (no loss,
  duplicate or reorder) → documented; applications that rely on loss detection
  may see different timing.
- **Thread and queue overhead per UDP source** could be abused by many local
  sources → bounded by `--max-streams` and `--max-connections-per-service`, and
  each queue is fixed-size.
- **Connected backend sockets ignore replies from a different address/port** →
  documented; a future option can use unconnected sockets if a real service
  needs it.
- **A new client talking to an old server** gets `OPEN_ERR` for a UDP service
  because the service does not exist there → clear authentication error; TCP
  remains compatible because the version is unchanged.
- **Extracting `SessionBase` could regress TCP** → the existing unit and
  integration suites must stay green before and after the extraction; the
  extraction lands as its own task with no behavior change.
- **Large datagram bursts can exhaust the Link's Channel window** → per-stream
  credits and bounded queues isolate the blast radius; the link still carries
  TCP traffic.

## Migration Plan

- Deploy the server first; UDP flags are additive and TCP-only peers are
  unaffected.
- Start using `--expose-udp` / `--forward-udp`; no configuration files or data
  migration are involved.
- Roll back by removing the UDP flags; existing TCP forwards and identities are
  untouched. `PROTOCOL_VERSION` is not bumped, so no identity or wire migration
  is needed.
- Bump the package version to `0.2.0` to mark the new UDP surface; the wire
  `PROTOCOL_VERSION` stays at 1.
