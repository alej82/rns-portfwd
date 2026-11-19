## Purpose

Defines how the server and client expose diagnostic detail through repeatable
verbosity flags, and keeps that detail useful and bounded: each level has an
observable effect, secrets stay redacted, and dropped UDP datagrams are reported
in summaries instead of one line each.

## ADDED Requirements

### Requirement: Verbosity levels

Both `rns-portfwd-server` and `rns-portfwd-client` SHALL map repeatable
`-v` / `--verbose` occurrences to logging levels with an observable effect at
each step:

| Occurrences | Application logger | Reticulum logger |
|---|---|---|
| `0` | `INFO` | `CRITICAL` |
| `1` | `DEBUG` | `NOTICE` |
| `2` or more | `DEBUG` | `DEBUG` |

The application level SHALL be `DEBUG` for any occurrence count of one or more.
The Reticulum level SHALL be `DEBUG` for any occurrence count of two or more.
Additional occurrences SHALL leave both levels at `DEBUG` and SHALL NOT be a
configuration error.

#### Scenario: Default verbosity

- **WHEN** a command runs without `-v`
- **THEN** the application logger emits `INFO` and above
- **AND** the Reticulum logger emits only critical messages

#### Scenario: One occurrence enables application debug

- **WHEN** a command runs with `-v`
- **THEN** the application logger emits `DEBUG` and above
- **AND** the Reticulum logger emits `NOTICE` and above but not `DEBUG`

#### Scenario: Two occurrences enable Reticulum debug

- **WHEN** a command runs with `-vv`
- **THEN** the application logger emits `DEBUG` and above
- **AND** the Reticulum logger emits `DEBUG` and above

#### Scenario: Extra occurrences saturate

- **WHEN** a command runs with `-vvv` or more
- **THEN** it starts normally with both loggers at `DEBUG`

### Requirement: Repeatable flags accumulate across argument positions

`-v` occurrences SHALL be counted across the command line regardless of whether
they appear before or after a subcommand. `-v -v` and `-vv` SHALL be equivalent,
and `rns-portfwd-client -v forward -v …` SHALL behave as `-vv`.

#### Scenario: Flags before and after the subcommand

- **WHEN** the client runs as `rns-portfwd-client -v forward -v …`
- **THEN** the effective occurrence count is two
- **AND** the Reticulum logger is enabled at `DEBUG`

#### Scenario: Combined short flags

- **WHEN** the client runs as `rns-portfwd-client forward -vv …`
- **THEN** the effective occurrence count is two

### Requirement: Secret redaction at every level

Every line emitted through the application logger SHALL pass through secret
redaction, including when the application level is `DEBUG`. A registered secret
SHALL never appear verbatim in application log output at any verbosity.

Reticulum's own logger is outside this guarantee. The documentation SHALL state
that `-vv` enables third-party output that is not filtered by the application's
redaction.

#### Scenario: Debug output redacts secrets

- **WHEN** a secret is registered and a `DEBUG` line would otherwise contain it
- **THEN** the emitted line contains the redaction placeholder instead

### Requirement: Bounded datagram-drop reporting

UDP drop accounting SHALL remain: every datagram discarded because it is
oversized or because a queue is full MUST still increment its counter.

The application SHALL NOT emit an unbounded one-line-per-datagram sequence for
repeated drops. It SHALL report drops through aggregated `DEBUG` summaries that
carry counts by reason (oversized and queue full). The first drop in a session
SHALL be logged immediately; subsequent drops within a reporting window SHALL be
collected and emitted as a single summary when the window elapses. Any pending
drops SHALL be flushed when the session closes, so a burst is never silently
lost.

Aggregation SHALL NOT change protocol behaviour: datagrams are still dropped
whole, never fragmented, truncated, coalesced, or allowed to block the listener
or other streams.

#### Scenario: Single drop is reported immediately

- **WHEN** a session drops one oversized datagram and then no more
- **THEN** the drop is logged at `DEBUG` without waiting for the reporting window
- **AND** the oversized counter is one

#### Scenario: Repeated drops are summarised

- **WHEN** many datagrams are dropped within one reporting window
- **THEN** at most one aggregated `DEBUG` summary is emitted for that window
- **AND** the summary distinguishes oversized drops from queue-full drops
- **AND** the counters match the number of dropped datagrams

#### Scenario: Pending drops are flushed on close

- **WHEN** a session accumulates drops that have not yet been summarised and then closes
- **THEN** the pending counts are emitted before the session close is reported

#### Scenario: Drops still preserve datagram semantics

- **WHEN** a datagram is dropped for size or queue pressure
- **THEN** no partial payload is delivered
- **AND** the next valid datagram in the session is still delivered
