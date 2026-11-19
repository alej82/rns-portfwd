## Why

Both commands already accept `-v/--verbose`, but a single `-v` only raises
Reticulum's log level: the application logger stays at `INFO` until `-vv`, so
the most common request ("show me more detail") appears to do nothing. The
Reticulum mapping is also off by one — it uses `LOG_NOTICE + verbosity`, which
lands on the non-existent level `5` at `-vv`, so full Reticulum debug actually
requires `-vvv`. None of this is documented.

## What Changes

- Fix the verbosity ladder so each flag has an observable effect:
  default `INFO`; `-v` raises the application logger to `DEBUG` and Reticulum
  to `NOTICE`; `-vv` additionally raises Reticulum to `DEBUG`; further `-v`
  saturates (and does not error).
- **Behavior change**: `-v` now emits application `DEBUG` lines (link ids,
  identity hashes, identity waits). These were already allowed at `debug` by the
  logging invariants and remain covered by secret redaction.
- Make the repeatable flag accumulate across argument positions, so
  `rns-portfwd-client -v forward -v …` is equivalent to `-vv` instead of `-v`.
- Replace the one-line-per-dropped-datagram `DEBUG` output with aggregated
  reporting: the first drop is logged immediately, subsequent drops are
  summarised periodically with counts by reason, and pending counts are flushed
  when the session closes. Drop counters and the protocol behaviour (drop,
  never fragment, never block) are unchanged.
- Document the logging levels, output stream, redaction guarantee, and the noise
  of Reticulum `DEBUG` in `README.md`, and give `-v/--verbose` real help text.

## Capabilities

### New Capabilities

- `logging`: verbosity levels for both commands, repeatable-flag semantics,
  secret-redaction guarantee at every level, and bounded datagram-drop logging.

### Modified Capabilities

None. The `udp-tunneling` requirements that drops are recorded and logged stay
satisfied: a single drop is still logged immediately, and bursts are logged in
summaries rather than per datagram.

## Impact

- `src/rns_portfwd/logging.py`: application level threshold and Reticulum level
  mapping.
- `src/rns_portfwd/cli_server.py`, `src/rns_portfwd/cli_client.py`: help text and
  verbosity accumulation across the root parser and subcommands.
- `src/rns_portfwd/datagrams.py` (and possibly `constants.py`): aggregated drop
  accounting and close-time flush.
- `README.md`: new logging/diagnostics section.
- `tests/unit/test_logging.py`, `tests/unit/test_datagrams.py`,
  `tests/unit/test_cli.py`: level mapping, flag counting, and aggregation.
- No protocol, wire-format, dependency, or configuration-file changes.
