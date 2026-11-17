## 1. Protocol and configuration foundations

- [x] 1.1 Add `MSG_DGRAM = 0xAC07`, `DEFAULT_UDP_IDLE_TIMEOUT = 60.0` and `DEFAULT_UDP_QUEUE = 256` to `constants.py`, plus a `DatagramMessage` (`stream_id`, `data`) in `protocol.py` and its registration; cover packing, unpacking, short frames and out-of-range ids in `tests/unit/test_protocol.py`
- [x] 1.2 Add `ChannelTransport.send_datagram(stream_id, data)` and `max_datagram()` (`channel.mdu - 2`); unit-test that a datagram is sent as one message and that the limit accounts for the header
- [x] 1.3 Add `protocol: str = "tcp"` to `Service` and `Forward`, make `Service.public()` report it, and add `parse_expose_udp` / `parse_forward_udp` with the same host/port/name/password rules; unit-test IPv6, colons in passwords, bad ports, duplicate names across protocols and duplicate local ports within one protocol
- [x] 1.4 Extract the shared session lifecycle (id, transport, logger, credit window, closed event, `abort`, `notify_closed`) into `SessionBase` in `streams.py` without behavior change, and run `python3 -m pytest tests/unit -q` plus the TCP integration suite green before continuing
- [x] 1.5 Make `Mux` store a `Session` protocol instead of a concrete `StreamSession`, dispatch `DatagramMessage` to `on_remote_datagram`, and unit-test that datagrams reach the right session and unknown ids are ignored

## 2. Datagram session

- [x] 2.1 Implement `DatagramSession` in `datagrams.py` with a bounded send queue, a sender thread that waits for full-datagram credit, a local write queue, `last_activity`, drop counters (oversized, queue full) and no EOF/half-close; unit-test credit waiting, oversized drop, queue-full drop and idle detection
- [x] 2.2 Implement local socket handling for both modes: an owned connected UDP socket (server) and a shared socket plus peer address (client), using `recvmsg` and `MSG_TRUNC` to detect truncated datagrams; unit-test with `socketpair`-style fakes or loopback sockets
- [x] 2.3 Add an idle reaper helper that closes sessions past `idle_timeout` by sending `CLOSE` and releasing their slot, with a unit test that one idle session is closed while a recently active one survives

## 3. Server

- [x] 3.1 Branch `PortfwdServer._open_backend` on `service.protocol`: resolve with `getaddrinfo`, create a connected `SOCK_DGRAM` socket, keep the existing acquire/reserve/install flow and send `OPEN_OK`; cover resolution and `connect` failures mapping to `BACKEND`
- [x] 3.2 Start the UDP receiver pump for a new UDP stream, send incoming datagrams with credit, and close only that session on `ECONNREFUSED`/`EHOSTUNREACH`; unit-test the session wiring with a fake transport and loopback backend
- [x] 3.3 Index link contexts by Reticulum `link_id` and mark the context authenticated on a successful `/probe` or `/list`, cancelling `--auth-timeout`; unit-test that a valid probe keeps the link and a failed one does not
- [x] 3.4 Add `--expose-udp` and `--udp-idle-timeout` to `cli_server.py`, validate the timeout is positive, and pass the timeout into the server; unit-test the argparse and validation paths
- [x] 3.5 Release the service slot and registry reservation on UDP idle close, link loss and shutdown; unit-test that the per-service counter returns to zero

## 4. Client

- [x] 4.1 Add a UDP listener path that binds `--bind:local_port` with `SOCK_DGRAM`, supports port `0`, prints the actual port in the same `name -> bind:port` format and closes on shutdown; unit-test bind failure as `BindError` and ephemeral port reporting
- [x] 4.2 Implement the per-source session table and pending-open handling in the client: create one stream per `(ip, port)`, queue datagrams while `OPEN` is in flight, avoid duplicate opens, and drop on queue overflow without blocking the receive loop; unit-test table eviction and pending limits
- [x] 4.3 Extend `_probe_all` to compare the service protocol returned by `/probe` with each forward and raise `AuthError` on a mismatch before listeners are created; unit-test TCP-to-UDP and UDP-to-TCP mismatches
- [x] 4.4 Start the UDP idle reaper, clear the source table on Link loss, and preserve listeners for reconnection; unit-test that sessions close on link loss and a later datagram recreates one
- [x] 4.5 Add `--forward-udp` and `--udp-idle-timeout` to `cli_client.py`, apply external secrets by service name, validate duplicates per protocol, and keep TCP `--forward` behavior unchanged; unit-test the CLI paths

## 5. Integration

- [x] 5.1 Add a UDP echo backend and UDP helpers (`udp_roundtrip`, UDP-aware `start_server`/`start_client`) to `tests/integration/harness.py`
- [x] 5.2 Cover one UDP forward end to end: single datagram, binary payload, datagram boundaries and ephemeral local port
- [x] 5.3 Cover two local UDP peers through one forward and assert isolated replies and distinct remote sessions
- [x] 5.4 Cover an oversized datagram (larger than `channel.mdu - 2`) being dropped while a following valid datagram is delivered, and assert no compression flag is used for datagrams
- [x] 5.5 Cover UDP idle close and recreation with a short `--udp-idle-timeout`, plus a UDP-only client surviving longer than `--auth-timeout`
- [x] 5.6 Cover TCP and UDP forwards sharing one Link, a Link drop closing UDP sessions while listeners stay up, and datagrams resuming after reconnect
- [x] 5.7 Cover the UDP service connection limit, unknown-service/wrong-password indistinguishability, protocol mismatch exit code and a datagram for an unknown stream being ignored
- [x] 5.8 Run `python3 -m pytest -q` and `ruff check src tests` clean

## 6. Documentation and close-out

- [x] 6.1 Document `--expose-udp`, `--forward-udp`, `--udp-idle-timeout`, the NAT-like mapping, the MDU size limit and the reliable/ordered delivery caveat in `README.md`
- [x] 6.2 Update `AGENTS.md` with the UDP module, `DGRAM` message and datagram invariants (boundaries, no fragmentation, credits, idle close)
- [x] 6.3 Run `openspec validate add-udp-tunneling --strict` and fix any finding
- [x] 6.4 Review the full diff for leaked secrets, TCP regressions and protocol-invariant violations, and record the verification commands in the change
- [x] 6.5 Bump the package version to `0.2.0` in `pyproject.toml` and `__init__.py` without changing `PROTOCOL_VERSION`

## Verification

- `python3 -m ruff check src tests` → all checks passed
- `python3 -m pytest tests/unit -q` → 91 passed
- `python3 -m pytest tests/integration -q` → 20 passed (TCP and UDP)
- `python3 -m pytest -q` → 111 passed
- `openspec validate add-udp-tunneling --strict` → valid
