## Why

Every exposed service currently requires a per-service password, so a server
cannot publish something meant for everyone (a community mirror, a status page,
a public file drop) without sharing a shared secret out of band. The only
alternative today is to hand the same password to every user, which destroys
revocation and accountability. A first-class, opt-in passwordless service lets
the operator decide per service what is public, keeps the existing
password-protected path untouched, and still gates enumeration behind
`--list-password`.

## What Changes

- Add `--expose-public HOST:PORT:NAME` and `--expose-udp-public HOST:PORT:NAME`
  to the server. These declare a service with no password; the existing
  `--expose` / `--expose-udp` grammar is unchanged.
- A public service accepts any `OPEN` password, including an empty one, so old
  clients and clients that send a stray password both connect. Private services
  keep the exact constant-time comparison they have today.
- The client gains `--forward-public NAME:LOCAL_PORT` and
  `--forward-udp-public NAME:LOCAL_PORT`. `probe` no longer requires
  `--service-password`: without it, the client sends an empty password, which
  succeeds only for public services.
- `/list` and `/probe` report public services with `auth: false`.
  `Service.public()` stops hardcoding `"auth": True`.
- Add `--max-connections-per-public` (default 8, lower than the 16 of
  `--max-connections-per-service`) so a public service is capped separately and
  an anonymous crowd cannot exhaust the per-service budget of private services.
- Public services are exempt from the secret-source rules: a secret document may
  coexist with public services and never needs an entry for them.
- Announces stay unchanged: they never reveal which services exist, and
  therefore never reveal which ones are public.
- **BREAKING** for API consumers of the dataclasses: `Service.password` and
  `Forward.password` become `str | None`, where `None` means public. The wire
  format is unchanged, so `PROTOCOL_VERSION` stays at 1.

## Capabilities

### New Capabilities
- `public-services`: passwordless exposure of TCP and UDP services, the
  `auth: false` reporting in `/list` and `/probe`, the separate public
  connection limit, and the secret-source exemptions.

### Modified Capabilities
<!-- No main specs exist yet; the capabilities introduced by add-udp-tunneling
     are still in its change directory, so this change only adds a new one. -->

## Impact

- Configuration/CLI: `config.py` (`Service.password: str | None`,
  `Forward.password: str | None`, `parse_expose_public`,
  `parse_expose_udp_public`, `parse_forward_public`,
  `parse_forward_udp_public`, secret-source exemptions in
  `apply_server_secrets` / `apply_forward_secrets`, relaxed
  `resolve_probe_password`), `cli_server.py` (`--expose-public`,
  `--expose-udp-public`, `--max-connections-per-public`), `cli_client.py`
  (`--forward-public`, `--forward-udp-public`, optional
  `--service-password`).
- Server: `registry.py` (`authenticate` public branch, per-class connection
  limits in `acquire`), `config.py` (`Service.public()` reports
  `auth: password is not None`), `server.py` passes the public limit through and
  stops registering `None` as a secret.
- Client: `client.py` sends `""` instead of `None` in `OPEN` and `/probe`, and
  `_probe_all` validates a public forward without a credential.
- Tests: unit coverage for parsing, validation, registry auth/limits, probe
  without a password, and server `OPEN` handling; integration coverage for a
  public TCP forward, a public UDP forward, mixed public/private on one `Link`,
  `auth: false` in `/list`, the public connection cap, and unknown-service
  indistinguishability.
- Docs: `README.md` (usage, security notes, limitations) and `AGENTS.md`
  (invariants for passwordless services).
