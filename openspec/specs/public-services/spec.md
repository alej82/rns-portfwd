# public-services Specification

## Purpose

Lets a server publish individual TCP and UDP services without a per-service
password, so anyone who knows the destination hash can use those services while
password-protected services and enumeration controls keep working unchanged.

## Requirements

### Requirement: Declaring a public service

The server SHALL accept `--expose-public HOST:PORT:NAME` and
`--expose-udp-public HOST:PORT:NAME` to expose a TCP or UDP backend that
requires no credential. The existing `--expose` and `--expose-udp` grammars
SHALL keep requiring a non-empty password and SHALL NOT change behavior.

Service names SHALL remain unique across public and password-protected
services, so a name selects exactly one backend. A malformed public definition
SHALL be rejected as a configuration error with a message naming the expected
`HOST:PORT:NAME` shape.

#### Scenario: Expose a public TCP service
- **WHEN** the server is started with `--expose-public 127.0.0.1:80:web`
- **THEN** the server starts and reports `web` among its service names
- **AND** a client can open a stream to `web` without supplying a password

#### Scenario: Public UDP service
- **WHEN** the server is started with `--expose-udp-public 127.0.0.1:5353:dns`
- **THEN** UDP sessions to `dns` are accepted without a password
- **AND** `/probe` and `/list` report it with protocol `udp`

#### Scenario: Duplicate name across public and private
- **WHEN** the same name is used by `--expose` and `--expose-public`
- **THEN** the server exits with the configuration exit code before listening
- **AND** the error identifies the duplicated name

#### Scenario: Malformed public definition
- **WHEN** `--expose-public` is given without a name, or with an empty host or a
  non-numeric port
- **THEN** the server exits with the configuration exit code
- **AND** the error states that `HOST:PORT:NAME` was expected

#### Scenario: IPv6 public backend
- **WHEN** `--expose-public '[::1]:443:web'` is used
- **THEN** the service is exposed with host `::1` and port `443`

### Requirement: Public services accept any credential

A public service SHALL authorize every `OPEN` request whose service name
matches, regardless of the password field, including an empty password. A
password-protected service SHALL keep comparing the supplied password in
constant time and SHALL reject a mismatch. An unknown service name SHALL keep
being indistinguishable from a wrong password for a password-protected
service: same error code, same delay, same failure accounting, and the same
eventual Link teardown.

A request that names a public service SHALL NOT increment the Link's
authentication-failure counter.

#### Scenario: Empty password reaches a public service
- **WHEN** a client sends `OPEN` for a public service with an empty password
- **THEN** the server connects the backend and replies `OPEN_OK`

#### Scenario: Stray password on a public service
- **WHEN** a client sends `OPEN` for a public service with a non-empty password
- **THEN** the server still connects the backend and replies `OPEN_OK`

#### Scenario: Private service still rejects a mismatch
- **WHEN** a client sends `OPEN` for a password-protected service with a wrong
  password
- **THEN** the server replies `OPEN_ERR` with the authentication code after the
  usual delay
- **AND** repeated failures close the Link as before

#### Scenario: Unknown service unchanged
- **WHEN** a client requests a service name that is not exposed
- **THEN** the response and timing are the same as for a wrong password on a
  private service
- **AND** the failure counts toward the Link's authentication limit

### Requirement: Public services are reported as unauthenticated

`/list` and `/probe` SHALL report a public service with `auth: false` and a
password-protected service with `auth: true`. `/list` SHALL keep requiring the
server's list password, and announcing SHALL keep carrying only the version,
display name, and enumeration flag, so announces reveal neither which services
exist nor which of them are public.

The client SHALL render `auth: no` for a public service in `list` and `probe`
output without changing the rest of the output format.

#### Scenario: Listing marks public services
- **WHEN** `/list` succeeds on a server exposing one public and one private
  service
- **THEN** the public entry has `auth: false`
- **AND** the private entry has `auth: true`

#### Scenario: Probe reports public auth
- **WHEN** `/probe` succeeds for a public service
- **THEN** the response marks the service with `auth: false`

#### Scenario: Announce does not disclose public services
- **WHEN** a server with `--announce` and public services emits an announce
- **THEN** the payload contains only the protocol version, display name, and
  enumeration flag
- **AND** it contains no service names

### Requirement: Client requests public forwards without a password

The client SHALL accept `--forward-public NAME:LOCAL_PORT` and
`--forward-udp-public NAME:LOCAL_PORT`, and SHALL send an empty password for
those forwards in `OPEN` and in the startup `/probe`. `probe` SHALL succeed
without `--service-password` against a public service and SHALL fail with the
authentication exit code against a password-protected one, reporting that the
service may require a password.

Public and password-protected forwards SHALL be mixable in one client process
on the same Link. Duplicate local ports SHALL keep being rejected per protocol
across both kinds of forward.

#### Scenario: Forward a public TCP service
- **WHEN** the client runs `--forward-public web:8080` against a server that
  exposes `web` publicly
- **THEN** the client probes `web` with an empty password, opens its listener on
  port 8080, and forwards connections

#### Scenario: Probe without a password
- **WHEN** the client runs `probe --service web` and `web` is public
- **THEN** the command succeeds and prints `auth=no`

#### Scenario: Probe without a password against a private service
- **WHEN** the client runs `probe --service db` and `db` requires a password
- **THEN** the command fails with the authentication exit code
- **AND** the error mentions that a service password may be required

#### Scenario: Mixed forwards on one Link
- **WHEN** the client forwards one public and one private service
- **THEN** both work over the same Link
- **AND** the private forward still sends its password

#### Scenario: Duplicate local port
- **WHEN** `--forward` and `--forward-public` request the same local TCP port
- **THEN** the client exits with the configuration exit code

### Requirement: Public services have their own connection limit

The server SHALL accept `--max-connections-per-public` (default 8, positive
integer) and apply it to the concurrent connections of each public service,
independently of `--max-connections-per-service` which continues to apply to
password-protected services. Connections beyond the applicable limit SHALL be
refused with the existing limit error code, and releasing a connection SHALL
return the slot to the correct counter.

#### Scenario: Public limit is enforced
- **WHEN** a public service has `--max-connections-per-public 2` and a third
  concurrent connection is attempted
- **THEN** the server replies with the limit error code for that stream
- **AND** the existing connections keep running

#### Scenario: Limits are independent
- **WHEN** a public service reaches its public limit while a private service is
  idle
- **THEN** new connections to the private service are still accepted

#### Scenario: Invalid public limit
- **WHEN** `--max-connections-per-public 0` is passed
- **THEN** the server exits with the configuration exit code

### Requirement: Public services are exempt from secret sources

When `--password-stdin` or `--secrets-fd` is used, public services SHALL NOT
require a password placeholder or an entry in the secret document, and SHALL
coexist with password-protected services that do use `-`. The same exemption
SHALL apply to `--forward-public` and `--forward-udp-public` on the client, and
the secret document SHALL never need to name a public service.

#### Scenario: Mixed secrets and public services
- **WHEN** the server uses `--password-stdin` with `--expose host:22:ssh:-` and
  `--expose-public host:80:web`
- **THEN** the server starts using the document entry for `ssh`
- **AND** `web` needs no document entry

#### Scenario: Public service missing from the document
- **WHEN** a secret document omits every public service name
- **THEN** the server starts normally

#### Scenario: Client secret source with a public forward
- **WHEN** the client uses `--password-stdin` with `--forward db:15432:-` and
  `--forward-public web:8080`
- **THEN** the private forward reads its password from the document
- **AND** the public forward sends an empty password
