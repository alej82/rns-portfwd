from __future__ import annotations

import io
import json
import os
import sys

import pytest

from rns_portfwd.config import (
    Forward,
    Service,
    apply_allow_identities,
    apply_forward_secrets,
    apply_server_secrets,
    parse_allow_identity,
    parse_expose,
    parse_expose_public,
    parse_expose_udp,
    parse_expose_udp_public,
    parse_forward,
    parse_forward_public,
    parse_forward_udp,
    parse_forward_udp_public,
    parse_identity_hash,
    read_secret_source,
    resolve_list_password,
    resolve_probe_password,
    validate_forwards,
    validate_services,
)
from rns_portfwd.errors import ConfigError

HASH_A = "0123456789abcdef0123456789abcdef"
HASH_B = "fedcba9876543210fedcba9876543210"
IDENTITY_A = bytes.fromhex(HASH_A)
IDENTITY_B = bytes.fromhex(HASH_B)


def test_parse_expose_password_with_colons():
    service = parse_expose("127.0.0.1:22:ssh:pa:ss:word")
    assert service == Service("127.0.0.1", 22, "ssh", "pa:ss:word")


def test_parse_expose_ipv6():
    service = parse_expose("[2001:db8::1]:2222:db:secret")
    assert service.host == "2001:db8::1"
    assert service.port == 2222
    assert service.name == "db"
    assert service.password == "secret"


def test_parse_expose_rejects_unbracketed_ipv6():
    with pytest.raises(ConfigError):
        parse_expose("2001:db8::1:22:ssh:secret")


def test_parse_expose_rejects_bad_fields():
    for value in (
        "host:22:ssh",
        "host:nope:ssh:secret",
        "host:0:ssh:secret",
        "host:70000:ssh:secret",
        "host:22:bad.name:secret",
        "host:22:ssh:",
    ):
        with pytest.raises(ConfigError):
            parse_expose(value)


def test_parse_udp_service_and_forward_set_protocol():
    service = parse_expose_udp("[::1]:5353:dns:pa:ss")
    assert service == Service("::1", 5353, "dns", "pa:ss", protocol="udp")
    assert service.public() == {"name": "dns", "protocol": "udp", "auth": True}
    forward = parse_forward_udp("dns:0:pa:ss")
    assert forward == Forward("dns", 0, "pa:ss", protocol="udp")


def test_parse_udp_rejects_bad_fields():
    for value in ("host:53:dns", "host:0:dns:x", "host:70000:dns:x", "bad.name:53:x"):
        with pytest.raises(ConfigError):
            parse_expose_udp(value)
    for value in ("dns:5353", "bad.name:5353:x", "dns:-1:x", "dns:70000:x"):
        with pytest.raises(ConfigError):
            parse_forward_udp(value)


def test_parse_forward_and_zero_port():
    forward = parse_forward("ssh:0:pa:ss")
    assert forward == Forward("ssh", 0, "pa:ss")


def test_parse_forward_rejects_invalid():
    for value in ("ssh:2222", "bad.name:2222:x", "ssh:-1:x", "ssh:70000:x"):
        with pytest.raises(ConfigError):
            parse_forward(value)


def test_validate_services_rejects_duplicates():
    services = [Service("h", 1, "ssh", "a"), Service("h", 2, "ssh", "b")]
    with pytest.raises(ConfigError):
        validate_services(services)
    with pytest.raises(ConfigError):
        validate_services(
            [
                Service("h", 1, "dns", "a"),
                Service("h", 2, "dns", "b", protocol="udp"),
            ]
        )


def test_validate_forwards_rejects_duplicate_ports_but_allows_zero():
    validate_forwards([Forward("a", 0, "x"), Forward("b", 0, "y")])
    with pytest.raises(ConfigError):
        validate_forwards([Forward("a", 10, "x"), Forward("b", 10, "y")])


def test_validate_forwards_allows_same_port_for_tcp_and_udp():
    validate_forwards(
        [Forward("a", 10, "x"), Forward("b", 10, "y", protocol="udp")]
    )
    with pytest.raises(ConfigError):
        validate_forwards(
            [
                Forward("a", 10, "x", protocol="udp"),
                Forward("b", 10, "y", protocol="udp"),
            ]
        )


def test_apply_server_secrets():
    services = [Service("h", 22, "ssh", "-")]
    document = {"list_password": "lp", "services": {"ssh": "sp"}}
    resolved, list_password = apply_server_secrets(services, "-", document, True)
    assert resolved[0].password == "sp"
    assert list_password == "lp"


def test_apply_secrets_preserve_protocol():
    services = [Service("h", 5353, "dns", "-", protocol="udp")]
    resolved, _ = apply_server_secrets(
        services, None, {"services": {"dns": "sp"}}, True
    )
    assert resolved[0].protocol == "udp"
    forwards = [Forward("dns", 5353, "-", protocol="udp")]
    resolved_forwards = apply_forward_secrets(
        forwards, {"services": {"dns": "pw"}}, True
    )
    assert resolved_forwards[0].protocol == "udp"


def test_server_secrets_reject_ambiguity_and_missing():
    services = [Service("h", 22, "ssh", "inline")]
    with pytest.raises(ConfigError):
        apply_server_secrets(services, None, {"services": {"ssh": "sp"}}, True)
    with pytest.raises(ConfigError):
        apply_server_secrets(
            [Service("h", 22, "ssh", "-")], None, {"services": {}}, True
        )


def test_apply_forward_secrets():
    forwards = [Forward("ssh", 22, "-")]
    resolved = apply_forward_secrets(
        forwards, {"services": {"ssh": "pw"}}, True
    )
    assert resolved[0].password == "pw"


def test_resolve_list_and_probe_passwords():
    assert resolve_list_password("inline", None, False) == "inline"
    assert resolve_list_password("-", {"list_password": "lp"}, True) == "lp"
    assert (
        resolve_probe_password("-", {"services": {"ssh": "sp"}}, True, "ssh")
        == "sp"
    )
    with pytest.raises(ConfigError):
        resolve_list_password("inline", {"list_password": "lp"}, True)


def test_read_secret_source_from_fd():
    read_fd, write_fd = os.pipe()
    os.write(write_fd, json.dumps({"services": {"ssh": "pw"}}).encode())
    os.close(write_fd)
    try:
        document, used = read_secret_source(False, read_fd)
    finally:
        os.close(read_fd)
    assert used is True
    assert document is not None
    assert document["services"]["ssh"] == "pw"


def test_read_secret_source_from_stdin(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"list_password": "lp"}'))
    document, used = read_secret_source(True, None)
    assert used is True
    assert document is not None
    assert document["list_password"] == "lp"


def test_parse_public_service_and_forward():
    service = parse_expose_public("127.0.0.1:80:web")
    assert service == Service("127.0.0.1", 80, "web", None)
    assert service.public() == {"name": "web", "protocol": "tcp", "auth": False}
    udp = parse_expose_udp_public("[::1]:5353:dns")
    assert udp == Service("::1", 5353, "dns", None, protocol="udp")
    assert parse_forward_public("web:8080") == Forward("web", 8080, None)
    assert parse_forward_udp_public("dns:0") == Forward("dns", 0, None, protocol="udp")


def test_parse_public_rejects_bad_fields():
    for value in (
        "host:80",
        "host:80:",
        "host:80:web:pw",
        ":80:web",
        "host:0:web",
        "host:70000:web",
        "host:80:bad.name",
        "[::1]:80",
    ):
        with pytest.raises(ConfigError):
            parse_expose_public(value)
    for value in ("web", "web:", "bad.name:8080", "web:-1", "web:70000"):
        with pytest.raises(ConfigError):
            parse_forward_public(value)


def test_public_names_stay_unique_across_visibility():
    with pytest.raises(ConfigError):
        validate_services(
            [Service("h", 22, "ssh", "s"), Service("h", 80, "ssh", None)]
        )
    validate_forwards(
        [Forward("web", 8080, None), Forward("dns", 8080, None, protocol="udp")]
    )
    with pytest.raises(ConfigError):
        validate_forwards([Forward("web", 8080, None), Forward("db", 8080, "pw")])


def test_public_services_are_exempt_from_secret_sources():
    services = [Service("h", 22, "ssh", "-"), Service("h", 80, "web", None)]
    resolved, list_password = apply_server_secrets(
        services, "-", {"list_password": "lp", "services": {"ssh": "sp"}}, True
    )
    assert [service.password for service in resolved] == ["sp", None]
    assert resolved[1].public()["auth"] is False
    assert list_password == "lp"

    resolved, list_password = apply_server_secrets(
        [Service("h", 80, "web", None)], None, None, False
    )
    assert resolved[0].password is None
    assert list_password is None


def test_public_service_needs_no_document_entry():
    services = [Service("h", 80, "web", None)]
    resolved, list_password = apply_server_secrets(services, None, {}, True)
    assert resolved[0].password is None
    assert list_password is None


def test_public_forwards_are_exempt_from_secret_sources():
    forwards = [Forward("db", 15432, "-"), Forward("web", 8080, None)]
    resolved = apply_forward_secrets(forwards, {"services": {"db": "pw"}}, True)
    assert [forward.password for forward in resolved] == ["pw", None]

    assert apply_forward_secrets(forwards[1:], None, False)[0].password is None


def test_resolve_probe_password_defaults_to_empty():
    assert resolve_probe_password(None, None, False, "web") == ""
    assert resolve_probe_password(None, {}, True, "web") == ""
    assert (
        resolve_probe_password("-", {"services": {"ssh": "sp"}}, True, "ssh")
        == "sp"
    )
    with pytest.raises(ConfigError):
        resolve_probe_password("", None, False, "web")


def test_read_secret_source_rejects_both_and_invalid(monkeypatch):
    with pytest.raises(ConfigError):
        read_secret_source(True, 3)
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    with pytest.raises(ConfigError):
        read_secret_source(True, None)


def test_service_restriction_property_and_public_auth():
    private = Service("h", 22, "ssh", "pw")
    public = Service("h", 80, "web", None)
    allowed = Service("h", 80, "web", None, allowed_identities=frozenset({IDENTITY_A}))
    assert private.is_restricted is True
    assert public.is_restricted is False
    assert allowed.is_restricted is True
    assert public.public()["auth"] is False
    assert private.public()["auth"] is True
    assert allowed.public()["auth"] is True


def test_service_keeps_positional_construction():
    assert Service("h", 22, "ssh", "pw") == Service(
        host="h", port=22, name="ssh", password="pw"
    )
    assert Service("h", 22, "ssh", "pw").allowed_identities == frozenset()


def test_parse_identity_hash_accepts_hex_case_insensitively():
    assert parse_identity_hash(HASH_A) == IDENTITY_A
    assert parse_identity_hash(HASH_A.upper()) == IDENTITY_A
    assert parse_identity_hash(f"  {HASH_B}  ") == IDENTITY_B


def test_parse_identity_hash_rejects_bad_values():
    for value in ("", "xyz", HASH_A[:-1], HASH_A + "0", HASH_A[:-1] + "g", "0x" + HASH_A[:30]):
        with pytest.raises(ConfigError) as excinfo:
            parse_identity_hash(value)
        assert "32" in str(excinfo.value)


def test_parse_allow_identity():
    name, identity = parse_allow_identity(f"ssh:{HASH_A}")
    assert name == "ssh"
    assert identity == IDENTITY_A
    for value in ("ssh", f"bad.name:{HASH_A}", f":{HASH_A}", "ssh:xyz", ""):
        with pytest.raises(ConfigError):
            parse_allow_identity(value)


def test_apply_allow_identities_accumulates_and_deduplicates():
    services = [
        Service("h", 22, "ssh", "pw"),
        Service("h", 80, "web", None),
    ]
    applied = apply_allow_identities(
        services,
        [("ssh", IDENTITY_A), ("ssh", IDENTITY_B), ("ssh", IDENTITY_A), ("web", IDENTITY_B)],
    )
    assert applied[0].allowed_identities == frozenset({IDENTITY_A, IDENTITY_B})
    assert applied[0].password == "pw"
    assert applied[1].allowed_identities == frozenset({IDENTITY_B})
    assert applied[1].password is None
    assert applied[1].public()["auth"] is True
    assert services[0].allowed_identities == frozenset()


def test_apply_allow_identities_merges_existing_entries():
    services = [
        Service("h", 22, "ssh", "pw", allowed_identities=frozenset({IDENTITY_A}))
    ]
    applied = apply_allow_identities(services, [("ssh", IDENTITY_B)])
    assert applied[0].allowed_identities == frozenset({IDENTITY_A, IDENTITY_B})


def test_apply_allow_identities_rejects_unknown_service():
    services = [Service("h", 22, "ssh", "pw")]
    with pytest.raises(ConfigError) as excinfo:
        apply_allow_identities(services, [("nosuch", IDENTITY_A)])
    assert "nosuch" in str(excinfo.value)
    assert apply_allow_identities(services, []) == services


def test_apply_server_secrets_preserves_allow_lists():
    services = [
        Service("h", 22, "ssh", "-", allowed_identities=frozenset({IDENTITY_A})),
        Service("h", 80, "web", None, allowed_identities=frozenset({IDENTITY_B})),
    ]
    resolved, _ = apply_server_secrets(
        services, None, {"services": {"ssh": "sp"}}, True
    )
    assert resolved[0].password == "sp"
    assert resolved[0].allowed_identities == frozenset({IDENTITY_A})
    assert resolved[1].allowed_identities == frozenset({IDENTITY_B})
    assert resolved[1].password is None

    unchanged, _ = apply_server_secrets(
        [Service("h", 22, "ssh", "inline", allowed_identities=frozenset({IDENTITY_A}))],
        None,
        None,
        False,
    )
    assert unchanged[0].allowed_identities == frozenset({IDENTITY_A})
