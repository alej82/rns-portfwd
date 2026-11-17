## MODIFIED Requirements

### Requirement: Public services are reported as unauthenticated

`/list` and `/probe` SHALL report an unrestricted public service with
`auth: false`, a public service that declares an identity allow-list with
`auth: true`, and a password-protected service with `auth: true`. `/list` SHALL
keep requiring the server's list password.

Announcing SHALL carry only the protocol version, display name, whether
enumeration is enabled, and — when link stamping is enabled — the advertised
link stamp cost. Announces SHALL NOT reveal service names, backend addresses,
identity hashes, which services are public, or which have an allow-list, so
announces reveal neither which services exist nor which of them are public.

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

#### Scenario: Announce carries the stamp cost
- **WHEN** a server with `--announce` and `--link-stamp-cost 12.5` emits an
  announce
- **THEN** its `app_data` contains the version, display name, enumeration flag,
  and `stamp_cost: 12.5`
- **AND** it contains no service names, identity hashes, or public/allow-list
  flags

#### Scenario: Announce does not disclose public services
- **WHEN** a server with `--announce` and public services emits an announce
- **THEN** the payload contains only the protocol version, display name,
  enumeration flag, and optional `stamp_cost`
- **AND** it contains no service names

#### Scenario: Allow-lists never appear in announces
- **WHEN** a server with `--announce` and allow-listed services emits an
  announce
- **THEN** the payload contains no service names and no identity hashes
