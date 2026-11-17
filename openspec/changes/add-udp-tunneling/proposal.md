## Why

`rns-portfwd` can only carry TCP streams, so services such as DNS, syslog,
metrics, VoIP signalling or game discovery cannot be reached over Reticulum.
UDP is datagram-oriented and does not fit the existing byte-stream session
model, so it needs its own wire message, session type and CLI surface rather
than a flag on the current TCP path.

## What Changes

- Add `--expose-udp HOST:PORT:NAME:PASSWORD` to the server. Each UDP service is
  authenticated with the same per-service password and appears in `/list` and
  `/probe` with `protocol: "udp"`.
- Add `--forward-udp NAME:LOCAL_PORT:PASSWORD` to the client. The local UDP
  listener maps every source address to an independent remote session
  (NAT-like), so several local peers can use one forward concurrently.
- Add a `DGRAM` Channel message (`0xAC07`) that carries exactly one datagram.
  Datagrams share the multiplexed `Link` with TCP streams and keep per-stream
  byte credits, but are never split or coalesced.
- Drop datagrams that do not fit in one Channel message or in the peer's credit
  window, and record the event. No application-level fragmentation in v1.
- Close idle UDP sessions after `--udp-idle-timeout` (default 60 s) on both the
  client and the server, and use a bounded per-session queue so one slow peer
  cannot grow memory or block other sessions.
- Validate the local forward protocol against `/probe` at startup, so a TCP
  forward aimed at a UDP service fails before any local traffic is accepted.
- Treat a successful `/probe` or `/list` as authentication for the server's
  `--auth-timeout`, so a UDP-only client does not lose its idle Link.
- Keep `PROTOCOL_VERSION` at 1: a new client only sends `DGRAM` after an
  `OPEN_OK`, and an old server rejects the `OPEN` first. No compression for
  datagrams. No TCP-like EOF, resumption or half-close for UDP.
- Document the UDP surface, limits and datagram semantics in `README.md`, and
  update `AGENTS.md` with the new module and protocol invariants.

## Capabilities

### New Capabilities
- `udp-tunneling`: exposing UDP services over Reticulum, forwarding local UDP
  traffic through peer-mapped sessions, and the datagram wire behavior
  (message, credits, size limits, idle close).

### Modified Capabilities
<!-- No main specs exist yet; this change introduces the first capability. -->

## Impact

- Wire protocol: `constants.py` (`MSG_DGRAM`, UDP defaults) and `protocol.py`
  (`DatagramMessage`), registered on both peers; `Mux` dispatches datagram
  messages to datagram sessions.
- Configuration/CLI: `config.py` (`Service.protocol`, `Forward.protocol`,
  `parse_expose_udp`, `parse_forward_udp`, protocol-aware validation),
  `cli_server.py`, `cli_client.py` (`--expose-udp`, `--forward-udp`,
  `--udp-idle-timeout`).
- Server: `server.py` opens connected UDP backend sockets, tracks UDP sessions
  against per-service limits, and authenticates the Link on successful
  `/probe` or `/list`.
- Client: `client.py` adds a shared UDP listener, per-peer session table,
  pending-open handling, idle reaper and protocol check; new
  `datagrams.py` with `DatagramSession`.
- Tests: unit coverage for parsing, the new message and session behavior, plus
  integration coverage with a UDP echo backend, multiple local peers, oversized
  datagrams, idle close, TCP+UDP on one Link and Link loss.
- Docs: `README.md`, `AGENTS.md`.
