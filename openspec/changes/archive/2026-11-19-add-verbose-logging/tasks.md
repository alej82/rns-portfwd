## 1. Logging ladder

- [x] 1.1 In `src/rns_portfwd/logging.py`, change `setup_logging` to select `DEBUG` for `verbosity >= 1` and `INFO` otherwise.
- [x] 1.2 Replace the arithmetic in `rns_loglevel` with explicit levels: `<= 0` → `RNS.LOG_CRITICAL`, `1` → `RNS.LOG_NOTICE`, `>= 2` → `RNS.LOG_DEBUG`.
- [x] 1.3 Add help text to `-v/--verbose` on the server and client parsers describing `-v` (application debug) and `-vv` (also Reticulum debug).

## 2. Repeatable flag accumulation

- [x] 2.1 In `src/rns_portfwd/cli_client.py`, declare `-v/--verbose` on the `common` parent with `dest="verbose_sub"` and `default=argparse.SUPPRESS`.
- [x] 2.2 Normalise `args.verbose += getattr(args, "verbose_sub", 0)` in `cli_client.main` before `setup_logging` and before passing `verbosity` to the client.
- [x] 2.3 Confirm the root parser still accepts `-v` before the subcommand and that `-vv` expands to two occurrences.

## 3. Datagram drop aggregation

- [x] 3.1 Add a `DROP_LOG_INTERVAL` (5 s) constant to `src/rns_portfwd/constants.py`.
- [x] 3.2 In `DatagramSession`, add pending counts per reason, a last-report timestamp, and a lock that guards counters and pending counts only.
- [x] 3.3 Add a `_record_drop(reason)` helper that increments the existing counters and pending counts, logs the first drop immediately, and emits a windowed summary when the interval elapses.
- [x] 3.4 Replace the five per-drop `logger.debug` call sites with `_record_drop` and remove the per-datagram lines.
- [x] 3.5 Flush pending drop counts from `_notify_closed` before delegating to `SessionBase`, keeping the existing counters intact.
- [x] 3.6 Verify no drop path changes behaviour: datagrams are still dropped whole and never block the listener.

## 4. Documentation

- [x] 4.1 Add a "Logging and diagnostics" section to `README.md` with the four-level table, the `stderr` destination, the redaction guarantee, and the note that `-vv` enables Reticulum output outside the application redaction filter.
- [x] 4.2 Mention that application `DEBUG` includes link ids and identity hashes, and that `-vv` is noisy.

## 5. Tests and verification

- [x] 5.1 Add unit tests for `setup_logging` levels (0 → `INFO`, 1 → `DEBUG`, 2 → `DEBUG`), clearing the global logger handlers around each call.
- [x] 5.2 Add unit tests for `rns_loglevel` (0 → `CRITICAL`, 1 → `NOTICE`, 2 and 3 → `DEBUG`).
- [x] 5.3 Add parser tests for `-v` before the subcommand, after it, both, and `-vv`.
- [x] 5.4 Add datagram tests with a capturing logger and patched clock: immediate first drop, one summary per window with reason split, counters, and close-time flush.
- [x] 5.5 Run `python3 -m pytest tests/unit -q` and then the full `python3 -m pytest -q`.
- [x] 5.6 Run `ruff check src tests`.
