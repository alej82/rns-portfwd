## Purpose

Lets a server require a Reticulum-native proof-of-work stamp before a `Link` is
created, and bounds half-open links, so anonymous link-request floods cost real
client CPU without changing the application protocol.

## ADDED Requirements

### Requirement: Server link stamp cost configuration

The server SHALL accept `--link-stamp-cost FLOAT` with a default of `0`, where
`0` disables stamping and a positive value requires a link-request stamp of at
least that cost. The value SHALL be between `0` and the Reticulum link cost cap
(`40`); a non-numeric, negative, or above-cap value SHALL be a configuration
error (exit code 2).

When the cost is positive and the installed Reticulum lacks native link-request
stamping (`RNS.Stamps` or `Destination.set_link_cost`), the server SHALL exit
with the configuration code and a message stating that `rns>=1.6` is required,
without opening any listener. The dependency floor SHALL remain
`rns>=1.5.7,<2`; a cost of `0` SHALL work on 1.5.x.

The configured cost SHALL apply to every incoming `Link` request to the server
destination. It SHALL NOT be selectable per service or per identity, because
native stamp validation happens before the service name or caller identity is
known.

#### Scenario: Stamping disabled by default
- **WHEN** the server starts without `--link-stamp-cost`
- **THEN** link requests are accepted exactly as before

#### Scenario: Positive cost accepted
- **WHEN** `--link-stamp-cost 12.5` is passed on Reticulum 1.6
- **THEN** the server starts and requires at least that stamp value

#### Scenario: Invalid cost
- **WHEN** `--link-stamp-cost` is negative, above `40`, or non-numeric
- **THEN** the server exits with the configuration code before starting

#### Scenario: Old Reticulum without native stamps
- **WHEN** `--link-stamp-cost 8` is used with a Reticulum that has no
  `RNS.Stamps` or no `Destination.set_link_cost`
- **THEN** the server exits with the configuration code
- **AND** the error states that `rns>=1.6` is required

### Requirement: Stamps are validated before the Link exists

When the cost is positive, the server SHALL validate the native link-request
stamp embedded in the request keys before allowing Reticulum to create the
`Link`, sign the proof, register it, or start its watchdog. Validation SHALL use
the Reticulum native algorithm over the request's signing public key and the
destination hash.

A request with a missing, malformed, or insufficient stamp SHALL be dropped
silently: no `Link` object, no proof, no application callback, no `OPEN_ERR`,
and no authentication failure accounting. The drop SHALL be logged only at
`debug`, so a flood cannot amplify logs. The server SHALL NOT reveal the
required cost, the received value, or any key material in the response.

With cost `0`, no stamp validation SHALL occur and legacy behavior SHALL be
preserved.

#### Scenario: Valid stamp creates the Link
- **WHEN** a request carries a stamp meeting the configured cost
- **THEN** Reticulum creates the Link as usual and the server can accept `OPEN`

#### Scenario: Missing or insufficient stamp
- **WHEN** a request has no stamp or one below the configured cost
- **THEN** the server drops it without creating a Link or sending a proof
- **AND** the client observes an establishment timeout, not an auth error

#### Scenario: Stamp validation precedes application code
- **WHEN** a stamped Link is created
- **THEN** no service password, allow-list, or `OPEN` check has run yet

### Requirement: Accepted stamps cannot be replayed immediately

The server SHALL keep a bounded cache of accepted `(ephemeral public key,
signing public key)` pairs and SHALL silently drop a request whose pair is
already cached. Entries SHALL expire after a fixed TTL (10 minutes) and the
cache SHALL have a fixed maximum size (16384 entries), evicting the oldest
entries when full.

The cache SHALL be protected against concurrent link requests and SHALL NOT
grow without bound. Replay rejection SHALL be indistinguishable from an invalid
stamp: silent drop, debug log only.

#### Scenario: First use is accepted
- **WHEN** a valid stamped request is seen for the first time
- **THEN** the Link is created and the pair is cached

#### Scenario: Immediate replay is rejected
- **WHEN** the same pair is used again within the TTL
- **THEN** the request is dropped and no new Link is created

#### Scenario: Cache expiry allows a new attempt
- **WHEN** the pair has been evicted by TTL or by the size limit
- **THEN** the same pair may be accepted again

### Requirement: Half-open link limits

The server SHALL accept `--max-half-open` (default `64`, `0` means unlimited)
and `--half-open-timeout FLOAT` (default `60` seconds, positive). Before
accepting a link request, the server SHALL count the destination's links in
`PENDING` and `HANDSHAKE`; at or above `--max-half-open` it SHALL drop the
request silently. After accepting a Link, it SHALL clamp that Link's
establishment timeout to `--half-open-timeout`.

These limits SHALL work with link stamping disabled and on Reticulum 1.5.x.
Invalid values SHALL be configuration errors (exit code 2). Dropped requests
SHALL NOT produce a proof, an application callback, or an auth error, and SHALL
be logged only at `debug`.

#### Scenario: Below the cap
- **WHEN** fewer than `--max-half-open` links are pending or in handshake
- **THEN** a new link request is accepted

#### Scenario: At the cap
- **WHEN** the number of pending/handshake links reaches `--max-half-open`
- **THEN** further link requests are dropped without a proof

#### Scenario: Half-open timeout is clamped
- **WHEN** a Link is accepted and its Reticulum default establishment timeout is
  longer than `--half-open-timeout`
- **THEN** its establishment timeout is reduced to the configured value

#### Scenario: Unlimited cap
- **WHEN** `--max-half-open 0` is passed
- **THEN** the count-based rejection is disabled

### Requirement: Announces advertise the link stamp cost

When `--link-stamp-cost` is positive, the server SHALL include a numeric
`stamp_cost` field in its announce `app_data`. When the cost is `0`, the field
SHALL be omitted. The field SHALL be present in path responses as well as
periodic announces, so a client can discover it without a periodic announce.

The announce SHALL still contain no service names, backend addresses, identity
hashes, public-service flags, or allow-list information. Reticulum peers that do
not understand the field SHALL ignore it.

#### Scenario: Cost is advertised
- **WHEN** a server runs with `--link-stamp-cost 12.5`
- **THEN** its announce `app_data` contains `stamp_cost: 12.5`

#### Scenario: Disabled cost is omitted
- **WHEN** the server runs with the default cost `0`
- **THEN** the announce `app_data` has no `stamp_cost` field

#### Scenario: Path response advertises the cost
- **WHEN** a client requests a path to a stamping server that is not announcing
- **THEN** the path response includes the same `stamp_cost`

### Requirement: Client discovers and applies the advertised cost

After resolving the server path, the client SHALL read the server's `app_data`
and parse `stamp_cost`. A valid cost SHALL be a number in `[0, 40]`; a missing,
out-of-range, non-numeric, or boolean value SHALL be treated as `0`.

When the effective cost is positive, the client SHALL configure Reticulum to
stamp its link requests at that cost before opening the `Link`, for `forward`,
`list`, `probe`, and every reconnect. `--link-stamp-cost FLOAT` SHALL override
the advertised value and `--no-link-stamp` SHALL disable stamping; using both
SHALL be a configuration error. A client-side cost SHALL be validated like the
server-side one (configuration exit code 2).

If the effective cost is positive but the installed Reticulum cannot generate
link stamps, the client SHALL fail fast with the resolution exit code before
opening a `Link`, stating that `rns>=1.6` is required. With cost `0` or no
advertised cost, the client SHALL NOT stamp and its current behavior SHALL be
unchanged.

#### Scenario: Automatic stamping
- **WHEN** the server advertises `stamp_cost: 8` and the client runs on
  Reticulum 1.6
- **THEN** the client stamps its link request at cost 8 and connects

#### Scenario: Manual override
- **WHEN** the client passes `--link-stamp-cost 12` against a stealth server
  with no advertised cost
- **THEN** it stamps at cost 12

#### Scenario: Stamping disabled on the client
- **WHEN** the client passes `--no-link-stamp` against a stamping server
- **THEN** no Link is established and the client reports a resolution/connect
  failure without touching any backend

#### Scenario: Conflicting client flags
- **WHEN** both `--link-stamp-cost` and `--no-link-stamp` are passed
- **THEN** the client exits with the configuration code

#### Scenario: Reticulum without stamp generation
- **WHEN** the server advertises a positive cost and the client's Reticulum
  cannot generate link stamps
- **THEN** the client exits with the resolution code before opening a Link
- **AND** the error mentions `rns>=1.6`

### Requirement: Wire and legacy compatibility

Link stamping SHALL NOT add Channel messages or change the `OPEN`, `DATA`,
`DGRAM`, `/list`, or `/probe` payloads. The link request size and format SHALL
remain unchanged, with the stamp embedded in the native request keys.

With cost `0`, clients and servers SHALL interoperate exactly as before.
With a positive cost, clients that cannot or do not stamp SHALL fail to
establish a Link; the server SHALL NOT send them an application-level
authentication error. Allow-list identities SHALL remain an additional
post-Link authorization layer and SHALL NOT exempt a client from the stamp.

#### Scenario: Application protocol unchanged
- **WHEN** cost is enabled
- **THEN** `OPEN`, `DATA`, `DGRAM`, `/list`, and `/probe` keep their existing
  encodings and tests pass unchanged

#### Scenario: Unstamped legacy client
- **WHEN** a Reticulum 1.5 client connects to a server with positive cost
- **THEN** no Link is established and no service request reaches the server

#### Scenario: Allow-list does not bypass the stamp
- **WHEN** an allow-listed client does not stamp against a stamping server
- **THEN** no Link is established
