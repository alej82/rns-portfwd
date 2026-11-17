## Purpose

Lets a server restrict each exposed service to a set of Reticulum identities,
independently of whether the service also checks a password, by validating the
cryptographic identity that the client presents on the Link.

## ADDED Requirements

### Requirement: Declaring per-service identity allow-lists

The server SHALL accept a repeatable `--allow-identity SERVICE:HASH` option,
where `HASH` is a 32-character hexadecimal Reticulum identity hash. Every
occurrence SHALL add one identity to that service's allow-list, so a service
may allow several identities. A service with no `--allow-identity` occurrence
SHALL keep its current authorization behavior, with no identity requirement.

`SERVICE` SHALL name a service declared by `--expose`, `--expose-udp`,
`--expose-public`, or `--expose-udp-public`; an unknown name SHALL be a
configuration error (exit code 2) reported before the server starts. A
non-hexadecimal, odd-length, or wrong-length hash SHALL also be a configuration
error naming the expected 32-hex format. Repeated identical pairs SHALL be
accepted and SHALL NOT change behavior.

Allow-lists SHALL be independent from secret sources: `--allow-identity` SHALL
work with inline passwords, with `-` placeholders resolved from a secret
document, and with passwordless public services.

#### Scenario: Allow-list on a private service
- **WHEN** the server is started with `--expose 127.0.0.1:22:ssh:s3cret` and
  `--allow-identity ssh:0123456789abcdef0123456789abcdef`
- **THEN** the server starts normally and reports `ssh` among its services

#### Scenario: Several identities for one service
- **WHEN** `--allow-identity db:HASH1 --allow-identity db:HASH2` is given
- **THEN** both identities are allowed to use `db`

#### Scenario: Allow-list with a secret source
- **WHEN** the server uses `--password-stdin` with `--expose host:22:ssh:-`
  and `--allow-identity ssh:HASH`
- **THEN** `ssh` requires both the document password and the allowed identity

#### Scenario: Unknown service in the allow-list
- **WHEN** `--allow-identity nosuch:HASH` names a service that is not exposed
- **THEN** the server exits with the configuration exit code
- **AND** the error identifies the unknown service name

#### Scenario: Malformed identity hash
- **WHEN** `--allow-identity ssh:xyz` or a hash shorter/longer than 32
  hexadecimal characters is passed
- **THEN** the server exits with the configuration exit code
- **AND** the error states that a 32-character hexadecimal identity hash is
  expected

### Requirement: Allow-lists authorize OPEN and UDP sessions

For a service with an allow-list, the server SHALL authorize an `OPEN` request
only when the Link initiator has proved possession of an allowed identity and,
for a service with a password, the password check also succeeds. UDP sessions
opened through the same `OPEN` path SHALL be subject to the same check.

A service with an allow-list SHALL reject a caller whose identity is not
allowed even if the password is correct. A passwordless service with an
allow-list SHALL keep ignoring the password field but SHALL still require an
allowed identity.

An identity rejection SHALL be indistinguishable from a wrong password for a
password-protected service: the same `OPEN_ERR` authentication code, the same
delay, the same increment of the Link's authentication-failure counter, and the
same eventual Link teardown. The server SHALL NOT log the rejected identity
hash at a level below `debug`, and SHALL never echo it in the error response.

An `OPEN` for an allow-listed service that arrives after Link establishment
SHALL be authorized when the identity proof is received within the Link's
identity wait period, which starts with the first pending `OPEN` on that Link
and lasts 5 seconds; later `OPEN`s share that deadline. If no identity proof
arrives in the period, the server SHALL reject every pending request with the
authentication error and failure accounting used for an unlisted identity.

#### Scenario: Allowed identity and correct password
- **WHEN** a client with an allowed identity opens `ssh` with the correct
  password
- **THEN** the server connects the backend and replies `OPEN_OK`

#### Scenario: Allowed identity but wrong password
- **WHEN** a client with an allowed identity opens a password-protected service
  with a wrong password
- **THEN** the server replies with the authentication error after the usual
  delay

#### Scenario: Correct password but unlisted identity
- **WHEN** a client with an identity that is not in the allow-list opens a
  service with the correct password
- **THEN** the server replies with the authentication error after the usual
  delay
- **AND** repeated attempts count toward the Link's authentication limit

#### Scenario: Anonymous client against an allow-listed service
- **WHEN** a client that never identifies itself opens an allow-listed service
- **THEN** the request is rejected with the authentication error after the
  identity wait period

#### Scenario: UDP service with an allow-list
- **WHEN** an unlisted identity opens a UDP service that has an allow-list
- **THEN** the UDP session is refused with the authentication error
- **AND** no backend socket is created

### Requirement: Identity allow-lists apply to probe and enumeration

`/probe` SHALL enforce the service allow-list for the requesting Link, so a
non-allowed identity receives the same generic `{"ok": false, "error":
"auth"}` response and delay as a wrong password or unknown service. `/list`
SHALL NOT filter services per caller; it SHALL keep listing every service to a
caller that presents the list password.

#### Scenario: Probe from an allowed identity
- **WHEN** an allowed identity probes an allow-listed service with a valid
  credential
- **THEN** the response is `{"ok": true, ...}`

#### Scenario: Probe from an unlisted identity
- **WHEN** an unlisted identity probes an allow-listed service with the correct
  password
- **THEN** the response and delay match a wrong password on that service

#### Scenario: Listing is not filtered
- **WHEN** an unlisted identity sends `/list` with the correct list password
- **THEN** the response contains every exposed service

### Requirement: Client presents a persistent identity on request

The client SHALL accept `--identity PATH` on `forward`, `list`, and `probe`.
When supplied, the client SHALL load the Reticulum identity from that path,
create it with owner-only permissions when it does not exist, and call the
Reticulum Link identification method on every Link it establishes, before
sending application messages. When omitted, the client SHALL remain anonymous
and SHALL NOT identify itself, preserving current behavior.

The client SHALL reuse the same identity across reconnects and SHALL NOT log
any private key material.

#### Scenario: Client identifies itself
- **WHEN** the client runs with `--identity /tmp/client.id`
- **THEN** the Link is created with that identity and the server sees it as
  identified before the first `OPEN`

#### Scenario: Missing identity file is created
- **WHEN** `--identity` points to a path that does not exist
- **THEN** the client creates it with mode `0600` and uses it

#### Scenario: Anonymous by default
- **WHEN** the client runs without `--identity`
- **THEN** it does not identify itself on the Link
- **AND** behavior against servers without allow-lists is unchanged

### Requirement: Identity hash helper

The client SHALL provide an `identity` subcommand that loads or creates the
identity at `--identity PATH` (defaulting to
`~/.config/rns-portfwd/client.id`), prints only the 32-character hexadecimal
identity hash on stdout, and exits with code 0 without requiring a running
Reticulum instance or a server.

#### Scenario: Print the hash to allow-list
- **WHEN** `rns-portfwd-client identity --identity /tmp/client.id` is run
- **THEN** stdout contains exactly one 32-hex hash line
- **AND** the same file later used with `--identity` presents that identity

#### Scenario: Default identity path
- **WHEN** `rns-portfwd-client identity` is run without `--identity`
- **THEN** it uses and prints the hash of `~/.config/rns-portfwd/client.id`
