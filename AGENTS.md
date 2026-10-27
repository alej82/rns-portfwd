# AGENTS.md

Guidance for coding agents working in this repository.

## Project

`rns-portfwd` is a Python package that forwards TCP through Reticulum. The server
exports named, password-protected services; the client resolves a 32-hex
destination hash, opens one `Link`, and multiplexes every local TCP connection
as a stream over that Link.

- User docs: `README.md`
- Package source: `src/rns_portfwd/`
- Tests: `tests/unit/` (no network) and `tests/integration/` (private `rnsd`)

## Environment

- Python `>=3.10`; the working environment is Python 3.11.14 from pyenv.
- Runtime dependency: `rns>=1.5.7,<2`.
- Always use the pyenv `python3` / `python3 -m pytest`.
- Do **not** use `/usr/bin/pytest`: it runs Python 3.14 without `RNS` installed.
- Do not add application config files. Runtime configuration is CLI flags plus
  optional JSON secrets via `--password-stdin` or `--secrets-fd`.
- Do not commit, push, or rewrite history unless the user explicitly asks.

## Commands

Run from the repository root:

```bash
# Install for development (already installed in the working environment)
python3 -m pip install -e . --no-deps --no-build-isolation

# Full suite (unit + integration, currently ~60 s)
python3 -m pytest -q

# Fast/unit-only
python3 -m pytest tests/unit -q
python3 -m pytest -m "not slow" -q

# Lint
ruff check src tests
```

The full suite includes `slow` integration tests (Link drop/reconnect and
slow-consumer isolation) by default. Integration tests launch their own `rnsd`
on loopback with isolated shared-instance ports; they must not touch or require
the system Reticulum daemon.

## Module map

| Module | Responsibility |
|---|---|
| `cli_server.py`, `cli_client.py` | Argument parsing, exit codes, process wiring |
| `config.py` | CLI validation, IPv6 endpoints, secret sources |
| `constants.py`, `protocol.py` | Wire constants and Channel message encode/decode |
| `server.py` | Destination, Link setup, `/list`, `/probe`, service auth, backend opens |
| `client.py` | Resolution, listeners, probes, forwarding, reconnect loop |
| `mux.py` | Per-Link stream table, 14-bit stream-id allocation, limits |
| `streams.py` | `ChannelTransport`, `StreamSession`, credits, compression, half-close |
| `credits.py` | Byte-credit accounting and window updates |
| `auth.py` | SHA-256 password comparison and failure tracking |
| `registry.py` | Exposed-service definitions and lookup |
| `discovery.py` | Announce payload encoding and validation |
| `link_manager.py` | Link lifecycle and reconnect/backoff helpers |
| `errors.py`, `logging.py` | Error types, exit codes, secret redaction |

`tests/integration/harness.py` owns the isolated `rnsd`, process wrappers, and
echo/bulk TCP backends. Reuse it instead of starting a system daemon.

## Protocol invariants

- Messages are `RNS.Channel` application messages in the `0xAC00` range:
  `OPEN=0xAC01`, `OPEN_OK=0xAC02`, `OPEN_ERR=0xAC03`, `DATA=0xAC04`,
  `WINDOW=0xAC05`, `CLOSE=0xAC06`.
- `OPEN` carries the 14-bit `stream_id`; do not drop or reorder it.
- Stream ids are monotonic and never reused on a Link. Exhaustion closes the
  Link and triggers reconnect.
- The server grants client-to-server credit in `OPEN_OK`. The client grants the
  reverse direction with a `WINDOW` immediately after `OPEN_OK`.
- Credits are per stream; a blocked consumer must not block other streams.
  Preserve half-close and byte-for-byte delivery.
- `ChannelTransport._send` retries `CEType.ME_LINK_NOT_READY` for up to 30 s and
  must stop retrying when the mux/link closes.
- Compression is opportunistic per payload (bz2, flag in `DATA`) and capped at
  1 MiB after decompression.
- One Link per server. On Link loss, live streams close, listeners remain, and
  the client reconnects with 1-60 s exponential backoff plus jitter. There is no
  TCP resumption.
- Servers are stealthy by default. `--announce` reveals only version, display
  name, and whether enumeration is enabled; never service names or backends.
- `/list` requires `--list-password`. `/probe` validates service credentials
  without connecting to the backend. Unknown service and wrong password must be
  indistinguishable.
- Never expose backend host/port or passwords in responses, announces, logs, or
  errors. Redact registered secrets.
- Address servers only by destination hash; display names are labels.
- Exit codes: `0` clean, `1` runtime/protocol, `2` config, `3` shared instance,
  `4` resolution/hash, `5` auth/probe, `6` bind.

## Code conventions

- Match existing style: `from __future__ import annotations`, type hints,
  dataclasses where appropriate, standard library first.
- Ruff config: line length 100, rules `E,F,I,UP,B,SIM` (`E501` and `SIM105`
  ignored). Keep imports sorted.
- Prefer small, testable functions. Add or update unit tests for parsing,
  validation, and edge cases; use integration tests only when real Reticulum
  behavior matters.
- Preserve existing public CLI behavior unless the change explicitly alters it;
  update `README.md` when flags, output, or security behavior changes.
- Do not log secrets. Test secret redaction and failure paths when touching auth
  or logging.


