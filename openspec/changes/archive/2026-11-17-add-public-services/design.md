## Context

Authentication today is uniform: `Service.password` and `Forward.password` are
non-empty strings, `_parse_expose` rejects an empty password, `Service.public()`
hardcodes `{"auth": True}`, and `ServiceRegistry.authenticate()` always compares
the supplied password in constant time. The `OPEN` message already carries a
variable-length password field, so an empty password is representable on the
wire and `PROTOCOL_VERSION` does not have to change: passwordless is a server
policy, not a wire feature.

The change has to keep three existing invariants intact: unknown service and
wrong password stay indistinguishable, enumeration stays behind
`--list-password`, and announces never reveal which services exist.

One implementation detail had to move with the feature: the 7-byte `OPEN`
header already carries a separate password length, and the decoder accepts a
zero length, but the encoder rejected empty strings. Allowing an empty password
field to be encoded keeps the byte layout and `PROTOCOL_VERSION` unchanged while
letting a public forward send a truly empty credential.

## Goals / Non-Goals

**Goals:**

- Declare public services per service, mixing freely with password-protected
  ones on the same server and the same `Link`.
- Accept any `OPEN` password for a public service, including an empty one, so
  existing clients can reach it without a new client build.
- Report `auth: false` in `/list` and `/probe` and in client output.
- Cap public services with a lower, independent connection limit.
- Keep the wire format, the announce payload and `PROTOCOL_VERSION = 1`.

**Non-Goals:**

- Client identity, allow-list, invite or rate-limit-beyond-concurrency
  authorization for public services.
- A server-wide "no passwords" switch.
- Publishing public services through announces or making them enumerable
  without `--list-password`.
- Changing how private services authenticate, or how secrets are read.

## Decisions

### Explicit per-service flags instead of a global switch

`--expose-public HOST:PORT:NAME` and `--expose-udp-public HOST:PORT:NAME` mirror
the existing `--expose` / `--expose-udp` pair. A global `--no-password` would
force every service public at once and make it impossible to mix, and allowing
an empty final field in `host:port:name:` would be a silent footgun next to the
`-` secret placeholder. Separate flags also match the precedent set by
`add-udp-tunneling` and keep the existing parser paths untouched.

### `password: str | None` as the single source of truth

Public is represented as `password=None` rather than a `public: bool` field or
an empty-string sentinel:

- `Service` and `Forward` stay frozen dataclasses with one credential field, so
  there is no way to express the contradictory "public with a password".
- `None` cannot be confused with the valid-but-rejected empty password; it also
  cannot be confused with the `-` secret placeholder.
- `register_secret()` already ignores falsy values, so None flows through the
  existing redaction path.

Dataclass field order is unchanged and `password` keeps no default, so existing
positional construction in tests keeps working; only the type widens.

### Public means "any credential", not "empty credential"

The registry returns the service for a public service before any comparison.
Rejecting a non-empty password would break older clients, whose CLI forces a
password, for no security benefit: a public service is public. Private
services keep `constant_time_equals` and `dummy_compare` exactly as they are, so
the unknown-service/wrong-password equivalence is preserved.

`_probe_all` sends `forward.password or ""` because the server's request handler
rejects a non-string `password` field; the same normalization applies to `OPEN`.

### Public failures do not count toward Link lockout

A successful open of a public service never increments `AuthTracker`, and no
public request can produce an `AUTH` error. That is the natural consequence of
accepting any credential, and it prevents an anonymous crowd from tripping the
per-Link failure limit.

### `auth: false` in authenticated responses, nothing new in announces

`Service.public()` returns `{"name", "protocol", "auth": password is not None}`.
`/list` still requires the list password, so only an operator-trusted client
learns which services are public. The announce payload keeps its existing
`{"v", "name", "auth"}` shape, where `auth` continues to mean "enumeration is
enabled"; reusing it for a different meaning would silently change what
announce scanners see, and leaking service-level facts contradicts the stealth
default.

### Separate, lower public connection limit

`--max-connections-per-public` defaults to 8 (`DEFAULT_MAX_CONNECTIONS_PER_PUBLIC`)
against 16 for `--max-connections-per-service`. `ServiceRegistry` picks the
limit per service class inside `acquire()` and keeps one counter per service
name, so the two classes never share a budget. Reusing the private limit was the
cheap option, but it lets anonymous traffic exhaust what the operator sized for
known users.

### Secret sources only cover private services

`apply_server_secrets` and `apply_forward_secrets` currently demand that every
inline password be `-` when a source is used. Public entries are excluded from
that check and from the document lookup: a public service has no secret, so
requiring a placeholder or an entry would be noise, and requiring the document
to omit them keeps the format unchanged.

### `probe --service NAME` without `--service-password`

The probe password becomes optional. Omitted means "send an empty password":
it succeeds for public services and fails with the authentication exit code for
private ones, with an error that mentions a password may be required. An explicit
`-` still reads the document, and an explicit inline value alongside a secret
source still raises the ambiguity error.

### Risks and trade-offs

- A public service is reachable by anyone who learns the destination hash. The
  only protections are the per-service public limit, `--max-links`,
  `--max-streams`, and the backend's own controls. Documented as a security
  note and as a limitation.
- Because stdout/`list` output gains `auth=no`, scripts that parse `list` output
  see a value they may not expect; the column already existed and was always
  `yes`.
- Two counters per service name (private and public limits) must both be
  released on every close path, including Link loss and UDP idle close; the
  existing reservation bookkeeping already centralizes this, and tests cover it.

## Migration

No wire or CLI breakage: existing `--expose`, `--forward`, secret documents and
exit codes behave identically. Only direct Python consumers of the `Service` and
`Forward` dataclasses see the wider `password` type and must handle `None`.

## Decisiones y supuestos

Decisiones confirmadas con el usuario antes de escribir la spec:

1. **Alcance:** modo público por servicio mediante flags nuevos
   (`--expose-public`, `--expose-udp-public`), no un interruptor global.
2. **Cliente:** flags simétricos `--forward-public` y `--forward-udp-public`;
   `probe` funciona sin `--service-password` contra servicios públicos.
3. **Visibilidad:** `/list` y `/probe` reportan `auth: false`; los announces no
   cambian y no revelan servicios públicos.
4. **Abuso:** límite separado `--max-connections-per-public`, con default más
   bajo (8) que `--max-connections-per-service` (16).

Supuestos asumidos (reversibles, no bloqueantes):

- El password de `OPEN` sigue siendo el único credential del cliente; no se
  añade identidad de cliente ni allow-list para servicios públicos.
- Un servicio público aparece en `/list` cuando la enumeración está habilitada,
  igual que hoy.
- No se requiere migración de configuración: los flags existentes no cambian.
