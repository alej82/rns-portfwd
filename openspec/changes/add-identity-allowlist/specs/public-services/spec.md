## MODIFIED Requirements

### Requirement: Public services accept any credential

A public service SHALL authorize every `OPEN` request whose service name
matches, regardless of the password field, including an empty password, except
when the service also declares an identity allow-list: in that case the request
SHALL additionally require an allowed Reticulum identity, as defined by the
`identity-allowlist` capability. A password-protected service SHALL keep
comparing the supplied password in constant time and SHALL reject a mismatch. An
unknown service name SHALL keep being indistinguishable from a wrong password
for a password-protected service: same error code, same delay, same failure
accounting, and the same eventual Link teardown.

A request that names a public service without an allow-list SHALL NOT increment
the Link's authentication-failure counter. A request that names a public
service with an allow-list SHALL increment it only when the identity is not
allowed, with the same delay and error as a wrong password.

#### Scenario: Empty password reaches a public service
- **WHEN** a client sends `OPEN` for a public service with an empty password
- **THEN** the server connects the backend and replies `OPEN_OK`

#### Scenario: Stray password on a public service
- **WHEN** a client sends `OPEN` for a public service with a non-empty password
- **THEN** the server still connects the backend and replies `OPEN_OK`

#### Scenario: Public service with an allow-list rejects an unlisted identity
- **WHEN** a public service has an allow-list and a client with an unlisted
  identity sends `OPEN` with an empty or arbitrary password
- **THEN** the server replies with the authentication error after the usual
  delay
- **AND** no backend connection is created

#### Scenario: Public service with an allow-list accepts an allowed identity
- **WHEN** a public service has an allow-list and a client with an allowed
  identity sends `OPEN` with an empty or arbitrary password
- **THEN** the server connects the backend and replies `OPEN_OK`

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

`/list` and `/probe` SHALL report an unrestricted public service with
`auth: false`, a public service that declares an identity allow-list with
`auth: true`, and a password-protected service with `auth: true`. `/list` SHALL
keep requiring the server's list password, and announcing SHALL keep carrying
only the version, display name, and enumeration flag, so announces reveal
neither which services exist, nor which of them are public, nor which have an
allow-list.

The client SHALL render `auth: no` for an unrestricted public service in `list`
and `probe` output without changing the rest of the output format.

#### Scenario: Listing marks public services
- **WHEN** `/list` succeeds on a server exposing one public and one private
  service, neither with an allow-list
- **THEN** the public entry has `auth: false`
- **AND** the private entry has `auth: true`

#### Scenario: Allow-listed public service reports auth
- **WHEN** `/list` succeeds on a server exposing a public service with
  `--allow-identity`
- **THEN** that entry has `auth: true`

#### Scenario: Probe reports public auth
- **WHEN** `/probe` succeeds for an unrestricted public service
- **THEN** the response marks the service with `auth: false`

#### Scenario: Allow-lists never appear in announces
- **WHEN** a server with `--announce` and allow-listed services emits an
  announce
- **THEN** the payload contains no service names and no identity hashes

#### Scenario: Announce does not disclose public services
- **WHEN** a server with `--announce` and public services emits an announce
- **THEN** the payload contains only the protocol version, display name, and
  enumeration flag
- **AND** it contains no service names

### Requirement: Public services have their own connection limit

The server SHALL accept `--max-connections-per-public` (default 8, positive
integer) and apply it to the concurrent connections of each public service that
has no identity allow-list, independently of `--max-connections-per-service`
which continues to apply to password-protected services. A public service that
declares an identity allow-list SHALL use `--max-connections-per-service`,
because access is no longer unrestricted. Connections beyond the applicable
limit SHALL be refused with the existing limit error code, and releasing a
connection SHALL return the slot to the correct counter. UDP sessions SHALL be
counted and limited by the same rule.

#### Scenario: Public limit is enforced
- **WHEN** a public service has `--max-connections-per-public 2` and a third
  concurrent connection is attempted
- **THEN** the server replies with the limit error code for that stream
- **AND** the existing connections keep running

#### Scenario: Allow-listed public service uses the service limit
- **WHEN** a public service with an allow-list has
  `--max-connections-per-service 3` and `--max-connections-per-public 1`
- **THEN** up to three allowed connections are accepted

#### Scenario: Limits are independent
- **WHEN** a public service reaches its public limit while a private service is
  idle
- **THEN** new connections to the private service are still accepted

#### Scenario: Invalid public limit
- **WHEN** `--max-connections-per-public 0` is passed
- **THEN** the server exits with the configuration exit code
