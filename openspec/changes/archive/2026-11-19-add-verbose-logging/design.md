## Context

See `proposal.md` — Why. The current implementation has two independent
verbosity axes that disagree:

- `setup_logging` selects `DEBUG` only at `verbosity >= 2`.
- `rns_loglevel` returns `LOG_NOTICE + verbosity` for `verbosity >= 2`, which is
  `5` at two occurrences. Reticulum compares `loglevel >= level` numerically and
  has no level `5` (`INFO=4`, `DEBUG=6`), so Reticulum `DEBUG` is only reachable
  at `-vvv`.
- `-v` is declared both on the root parser and on the `common` parent of the
  client subcommands. The subparser parses into a separate namespace and then
  overwrites the root value, so occurrences on both sides do not add up.
- `DatagramSession` logs one `DEBUG` line per dropped datagram from three
  threads, while already keeping `dropped_oversized` and `dropped_queue_full`
  counters that are never summarised.

The application logger is created once per process and short-circuits when it
already has handlers; Reticulum is configured separately through
`init_reticulum(..., verbosity)`.

## Goals / Non-Goals

**Goals:**

- A predictable two-axis ladder: application detail at `-v`, Reticulum detail at
  `-vv`, no dead levels.
- Flag counting that behaves like a single repeatable option.
- Drop diagnostics that stay useful without growing with drop rate.
- No changes to protocol behaviour, wire format, or configuration files.

**Non-Goals:**

- A `-q/--quiet` flag, JSON/structured logs, log files, or periodic statistics.
- Per-packet/per-message tracing.
- Filtering or reinterpreting Reticulum's own log output.

## Decisions

### Application level at one occurrence

`setup_logging` uses `DEBUG` when `verbosity >= 1`, otherwise `INFO`. Keeping
`INFO` as the default preserves current behaviour for users who pass no flag;
moving the application jump to `-v` matches the intuitive meaning of
"verbose".

Alternative considered: leave the threshold at `>= 2` and only document `-vv`.
Rejected because the common case (`-v`) would still look inert.

### Reticulum mapping by explicit levels

Replace the arithmetic with explicit branches:

- `verbosity <= 0` → `RNS.LOG_CRITICAL`
- `verbosity == 1` → `RNS.LOG_NOTICE`
- `verbosity >= 2` → `RNS.LOG_DEBUG`

This preserves what `-v` already did, makes `-vv` reach actual `DEBUG`, and
removes the non-existent level `5`. `RNS.LOG_INFO` remains deliberately unused:
the flag exposes four useful states (quiet, notices, application debug,
application + Reticulum debug), not every Reticulum constant.

Alternative considered: map `-v` to `LOG_INFO` and `-vv` to `LOG_DEBUG`.
Rejected because it would make the first `-v` noisier than today while the
application still had only one useful jump.

### Accumulate counts with two destinations

The root parser keeps `-v/--verbose` on `verbose`. The client `common` parent
declares the same flags with `dest="verbose_sub"` and `default=argparse.SUPPRESS`.
Each `main` normalises once after parsing:

```
args.verbose += getattr(args, "verbose_sub", 0)
```

before calling `setup_logging` and passing `verbosity` to the server/client. The
server CLI has no subcommands, so only the help text changes there. The
`identity` subcommand intentionally does not accept `-v` after the subcommand
(it prints one hash and exits); a root-level `-v` is still accepted.

Alternative considered: a custom `argparse.Action` that increments the
namespace. Rejected because `_SubParsersAction` parses the subcommand into a
fresh namespace, so the child action cannot see the root count; making it work
requires a shared mutable default that would leak state between invocations and
tests.

Alternative considered: pre-scanning `argv`. Rejected because it must
re-implement combined short-flag handling (`-vv`) that argparse already does.

### Windowed drop summaries with immediate first report

`DatagramSession` keeps the existing counters and adds pending counts per reason
(`oversized`, `queue_full`), a last-report timestamp, and a small lock used only
to update counters/pending and snapshot values. The logger is called outside the
lock.

- Every drop increments its counter and its pending count.
- If no summary has been emitted yet, the drop is logged immediately.
- Otherwise, a summary is emitted when at least `DROP_LOG_INTERVAL` (a new
  constant, 5 s) has elapsed. The summary reports the counts accumulated since
  the previous report, split by reason, at `DEBUG`.
- `_notify_closed` flushes any pending counts before delegating to the base
  implementation, so a short burst at session end is still visible.
- The existing per-drop call sites collapse into one `_record_drop(reason)`
  helper; no drop path is removed.

Alternative considered: report drops only in the close line. Rejected because
idle sessions can live for minutes and an operator watching a live problem would
see nothing.

Alternative considered: keep per-drop lines and rely on log rotation. Rejected
because a synchronous `stderr` line per datagram can dominate CPU under exactly
the overload that causes drops.

### Reticulum output stays outside redaction

`RedactionFilter` is attached to the `rns_portfwd` logger only. Reticulum owns
its own logging and destination. Wrapping or monkey-patching it would be
fragile and version-dependent. No secret is sent through Reticulum log calls
today, so the design documents the boundary instead of filtering it.

### Verification strategy

- Unit tests for `setup_logging` levels and `rns_loglevel` mapping. The
  `rns_portfwd` logger is global, so tests remove its handlers around each call
  (and restore `propagate`) to avoid order dependence.
- Parser tests: `-v` before the subcommand, after it, and both; `-vv` equals
  `-v -v`.
- Datagram tests: a capturing logger plus a patched `time.monotonic` verifies
  immediate first drop, one summary per window, reason split, counters, and
  close-time flush.
- A README section and `--help` text are checked by review; no runtime test.

## Risks / Trade-offs

- `-v` becomes noisier (application `DEBUG`) → documented as the intended
  meaning; drop aggregation removes the worst source of per-event spam.
- Application `DEBUG` includes link ids and identity hashes → already permitted
  by the project invariant ("never below `debug`") and still redacted for
  secrets.
- `-vv` enables very chatty third-party output → documented; users who only
  need application detail stay at `-v`.
- The global logger short-circuit can make tests order-dependent → explicit
  handler cleanup in the logging tests.
- Two destinations for one flag could drift → normalisation is a single line in
  each CLI entry point and is covered by parser tests.
- Drop summaries lose per-datagram sizes → totals by reason are what operators
  need; individual oversized sizes were already only diagnostic noise.

## Migration Plan

No configuration or wire changes. Existing invocations keep working:

- `-vv` users keep application `DEBUG` and gain actual Reticulum `DEBUG`.
- `-vvv` users can drop to `-vv`; extra occurrences remain harmless.
- Users who want the old application `INFO` at `-v` simply omit the flag.

Rollback is a revert of the logging, CLI, and datagram changes; no state or data
migration is involved.
