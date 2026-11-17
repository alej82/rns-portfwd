## 1. Configuration and data model

- [x] 1.1 Widen `Service.password` and `Forward.password` to `str | None` in `config.py` (`None` = public), make `Service.public()` return `"auth": self.password is not None`, and add `DEFAULT_MAX_CONNECTIONS_PER_PUBLIC = 8` to `constants.py`; run `tests/unit/test_registry.py` and `tests/unit/test_config.py` to confirm the widened type alone does not break existing construction
- [x] 1.2 Add `parse_expose_public` / `parse_expose_udp_public` accepting `HOST:PORT:NAME` (IPv6 brackets included, no password field, no `validate_secret` call) and `parse_forward_public` / `parse_forward_udp_public` accepting `NAME:LOCAL_PORT`; unit-test valid TCP/UDP/IPv6 forms, empty host, missing name, non-numeric and out-of-range port, and that the error message states the expected shape
- [x] 1.3 Keep names unique across public and private services in `validate_services`, and local ports unique per protocol across both forward kinds in `validate_forwards`; unit-test the two collisions and that a TCP public and a UDP public forward may share a port
- [x] 1.4 Exempt public entries from secret-source rules: `apply_server_secrets` and `apply_forward_secrets` skip `password is None` entries in the `-` requirement and in document lookups, while private entries keep the existing ambiguity errors; unit-test public+`-` mixing, a public name absent from the document, and a public name that has an entry (ignored)
- [x] 1.5 Make `resolve_probe_password` return `""` when `--service-password` is omitted with no secret source, keep `-` reading the document, and keep the inline+source ambiguity error; unit-test all four paths
- [x] 1.6 Allow the `OPEN` encoder to emit an empty password field (length 0) while keeping an empty service name invalid; unit-test that an empty password round-trips and that the byte layout is unchanged

## 2. Registry and server authentication

- [x] 2.1 Return the service directly from `ServiceRegistry.authenticate` when its password is `None`, before any comparison, and keep `dummy_compare` plus the constant-time path for private and unknown services; unit-test empty, arbitrary and missing password for public services and that unknown names still fail
- [x] 2.2 Add a public connection limit to `ServiceRegistry` (constructor argument, default 8) and select the limit per service class inside `acquire()` while keeping one counter per name; unit-test that a public service stops at its own limit, a private service is unaffected, `active_connections` stays correct, and `release` returns the slot
- [x] 2.3 Pass `max_connections_per_public` from `PortfwdServer` into the registry and verify a successful `OPEN` for a public service does not touch `AuthTracker` (no failure increment, no `AUTH` error, no delayed teardown); unit-test in `tests/unit/test_server_handlers.py` with an empty and a non-empty password
- [x] 2.4 Confirm `_handle_open` still replies `LIMIT` when a public service is saturated and releases the slot on every close path (backend failure, link loss, shutdown); extend `tests/unit/test_server_handlers.py` accordingly

## 3. Server CLI

- [x] 3.1 Add `--expose-public` and `--expose-udp-public` (metavar `HOST:PORT:NAME`) to `cli_server.py`, wire them into `main` alongside the existing parsers, and update the examples block with one public service; unit-test argparse acceptance and that a public service needs no secret source
- [x] 3.2 Add `--max-connections-per-public` (default `DEFAULT_MAX_CONNECTIONS_PER_PUBLIC`) with a positive-integer validation, pass it to `PortfwdServer`, and unit-test the default, a custom value and the rejection of `0`
- [x] 3.3 Make `register_secret` calls tolerate `None` passwords in `cli_server.py` and confirm no log line, error or announce ever contains a public service's name or backend address; unit-test redaction is unaffected
- [x] 3.4 Print the public/private distinction at startup only through the existing `services: ...` line; keep stdout format unchanged so existing parsers do not break, and update the CLI help text for the new flags

## 4. Client

- [x] 4.1 Add `--forward-public` and `--forward-udp-public` (metavar `NAME:LOCAL_PORT`) to `cli_client.py`, parse them with the new helpers, and register only non-`None` passwords; unit-test parser acceptance, mixed public/private forwards and duplicate-port rejection
- [x] 4.2 Normalize the credential at the wire boundary in `client.py`: send `forward.password or ""` in every `send_open` call and in the `_probe_all` request, and keep the startup `/probe` protocol check for public forwards; unit-test with a fake transport that a public forward sends an empty password and that a protocol mismatch still fails
- [x] 4.3 Allow `probe` without `--service-password`, send an empty password, and report the authentication failure with a hint that the service may require a password; unit-test public success (`auth=no`), private failure and the exit code
- [x] 4.4 Keep `list` output unchanged except that public entries render `auth=no`; unit-test `_print_services` and `run_probe` formatting

## 5. Integration

- [x] 5.1 Extend `tests/integration/harness.py` with helpers to start a server exposing public TCP/UDP services and a client using public forwards, reusing the existing private `rnsd` isolation
- [x] 5.2 Cover a public TCP forward end to end: `--expose-public` plus `--forward-public`, byte-for-byte echo, and a CLI without any password flag
- [x] 5.3 Cover a public UDP forward end to end, including the startup `/probe` with an empty password
- [x] 5.4 Cover mixed public and private services on one `Link`: the private forward still authenticates, a wrong private password still fails, and an unknown service stays indistinguishable
- [x] 5.5 Cover the public connection limit: `--max-connections-per-public` refuses the extra connection with the limit error while private service slots stay available
- [x] 5.6 Cover `/list` reporting `auth: false` for the public service and `auth: true` for the private one, and `probe` succeeding without a password only for the public service
- [x] 5.7 Verify no regression in announces: with `--announce` and a public service, the payload contains no service name and no public flag

## 6. Docs and verification

- [x] 6.1 Update `README.md`: usage examples with `--expose-public` / `--forward-public`, the `auth=no` semantics, the public connection limit, the secret-source exemption, security notes (anyone with the hash can use a public service; no client identity) and the limitations list
- [x] 6.2 Update `AGENTS.md` protocol invariants: public services accept any `OPEN` password, report `auth: false`, use the separate limit, and never appear in announces
- [x] 6.3 Run `PYTHONPATH=src python3 -m pytest tests/unit -q`, then the full `PYTHONPATH=src python3 -m pytest -q`, and `ruff check src tests`; both must pass with the new tests included
- [x] 6.4 Run `openspec validate add-public-services --strict` and fix any spec formatting issue before reporting the change as ready
