# udp-tunneling Specification

## Purpose

Lets users reach UDP-only services over Reticulum by exposing named UDP
backends and mapping each local UDP peer to an isolated datagram session on a
shared Link.

## Requirements

### Requirement: Expose UDP services

The server SHALL accept a repeatable `--expose-udp HOST:PORT:NAME:PASSWORD`
option with the same host, port, name and password rules as `--expose`.
Service names MUST be unique across TCP and UDP services. A UDP service MUST
use the same per-service password authentication and the same indistinguishable
failure behavior as a TCP service.

#### Scenario: UDP service is exposed and listed
- **WHEN** the server runs with `--expose-udp 127.0.0.1:5353:dns:s3cret --list-password lp`
- **THEN** `/list` with the correct list password reports `dns` with protocol `udp` and authentication enabled

#### Scenario: Duplicate name across protocols is rejected
- **WHEN** the server is configured with both `--expose 127.0.0.1:22:ssh:s3cret` and `--expose-udp 127.0.0.1:5353:ssh:other`
- **THEN** startup fails with a configuration error before the destination is announced

#### Scenario: UDP authentication failure is indistinguishable
- **WHEN** an `OPEN` names an unknown service or a known UDP service with a wrong password
- **THEN** the server returns the same error code after the same delay in both cases

### Requirement: Local UDP forwarding

The client SHALL accept a repeatable `--forward-udp NAME:LOCAL_PORT:PASSWORD`
option. It MUST bind a local UDP socket on `--bind` and print the actual local
port, including an ephemeral port when `0` is requested. Each distinct local
source address and port MUST map to its own remote session, and responses MUST
be delivered only to the source that created the session. Multiple UDP forwards
MUST share the single server Link.

#### Scenario: Datagram round trip
- **WHEN** a local process sends a datagram to the forwarded port and the backend replies
- **THEN** the reply arrives at the same local source address and port with identical bytes

#### Scenario: Two local peers are isolated
- **WHEN** two local sockets send datagrams through the same forward
- **THEN** each reply returns only to its originating socket and each peer uses a distinct remote session

#### Scenario: Ephemeral local port
- **WHEN** the client forwards with local port `0`
- **THEN** it prints the port assigned by the operating system and accepts datagrams on it

#### Scenario: Multiple forwards share one Link
- **WHEN** the client runs TCP and UDP forwards to the same server
- **THEN** all forwards use the already established Link rather than opening additional Links

### Requirement: Forward protocol validation

At startup the client SHALL confirm each service's protocol through `/probe`.
If a local forward's protocol does not match the exposed service protocol, the
client MUST fail with the authentication exit code before binding or accepting
local traffic. `/list` and `/probe` SHALL report the service protocol.

#### Scenario: Mismatched forward is rejected
- **WHEN** the client uses `--forward-udp` for a service exposed with `--expose`
- **THEN** it exits with the authentication code and never opens the local UDP listener

#### Scenario: Probe reports the protocol
- **WHEN** the client probes a UDP service with the correct password
- **THEN** the response identifies the service as protocol `udp`

### Requirement: Datagram boundaries and size limits

Each datagram MUST travel as exactly one datagram message: the tunnel MUST NOT
split, coalesce or truncate user payloads, and MUST preserve their order within
a session. A datagram that does not fit in one Channel message or in the peer's
available credit window MUST be dropped and recorded; it MUST NOT be
fragmented, truncated or partially delivered. Datagrams MUST NOT be
compressed.

#### Scenario: Boundaries are preserved
- **WHEN** a peer sends two datagrams of different sizes
- **THEN** the backend receives two separate datagrams with the original sizes and contents

#### Scenario: Oversized datagram is dropped
- **WHEN** a peer sends a datagram larger than one Channel message can carry
- **THEN** the tunnel logs the drop, delivers no partial payload, and still delivers the next valid datagram

#### Scenario: No datagram compression
- **WHEN** compression is enabled for TCP streams
- **THEN** UDP datagrams are sent without per-payload compression

### Requirement: Idle sessions and resource limits

Both peers SHALL close a UDP session after `--udp-idle-timeout` seconds
without datagrams in either direction, defaulting to 60 seconds. Each UDP
session MUST count toward `--max-streams` and the per-service connection limit.
A full session queue MUST drop new datagrams instead of blocking the listener,
other sessions or TCP streams, and the drop MUST be recorded.

#### Scenario: Idle session closes and is recreated
- **WHEN** a local peer is silent for longer than the idle timeout and then sends again
- **THEN** the old session is closed and a new session carries the later datagram

#### Scenario: Service connection limit applies to UDP
- **WHEN** a UDP service has reached `--max-connections-per-service` active sessions
- **THEN** a new local source receives a limit rejection and no backend datagram

#### Scenario: One busy peer does not block another
- **WHEN** one local peer stops reading while another peer and a TCP stream are active
- **THEN** the other UDP peer and the TCP stream continue to transfer data

### Requirement: Link lifecycle

UDP sessions MUST close when the Link is lost and MUST NOT be resumed after
reconnection; listeners remain available and new datagrams create new sessions.
A Link that has authenticated successfully through `/probe` or `/list` MUST NOT
be torn down by `--auth-timeout`. Datagrams for a stream that was never opened
or has been closed MUST be ignored.

#### Scenario: UDP-only client keeps its idle Link
- **WHEN** a client forwards only UDP services and no datagram is sent for longer than `--auth-timeout`
- **THEN** the server keeps the Link because the startup probe authenticated it

#### Scenario: Link loss closes UDP sessions
- **WHEN** the Link drops while UDP sessions are active
- **THEN** those sessions close, listeners stay bound, and a datagram after reconnection opens a new session

#### Scenario: Datagram for unknown stream is ignored
- **WHEN** a peer sends a datagram for a stream id with no installed session
- **THEN** the receiver ignores it without closing the Link or other streams

### Requirement: UDP privacy

UDP services MUST obey the same disclosure rules as TCP services: `/list`,
`/probe`, announces, logs and errors MUST NOT reveal backend host, port or
password, and announces MUST NOT include UDP service names. A client MUST send
datagrams only through a session it has authenticated.

#### Scenario: Listing does not leak the backend
- **WHEN** a client lists or probes a UDP service
- **THEN** the response contains only the service name, protocol and authentication flag

#### Scenario: Logs redact UDP secrets
- **WHEN** a UDP service password appears in a failure path
- **THEN** logs and error messages contain the redacted placeholder instead of the password
