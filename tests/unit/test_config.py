from __future__ import annotations

import io
import json
import os
import sys

import pytest

from rns_portfwd.config import (
    Forward,
    Service,
    apply_forward_secrets,
    apply_server_secrets,
    parse_expose,
    parse_forward,
    read_secret_source,
    resolve_list_password,
    resolve_probe_password,
    validate_forwards,
    validate_services,
)
from rns_portfwd.errors import ConfigError


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


def test_validate_forwards_rejects_duplicate_ports_but_allows_zero():
    validate_forwards([Forward("a", 0, "x"), Forward("b", 0, "y")])
    with pytest.raises(ConfigError):
        validate_forwards([Forward("a", 10, "x"), Forward("b", 10, "y")])


def test_apply_server_secrets():
    services = [Service("h", 22, "ssh", "-")]
    document = {"list_password": "lp", "services": {"ssh": "sp"}}
    resolved, list_password = apply_server_secrets(services, "-", document, True)
    assert resolved[0].password == "sp"
    assert list_password == "lp"


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


def test_read_secret_source_rejects_both_and_invalid(monkeypatch):
    with pytest.raises(ConfigError):
        read_secret_source(True, 3)
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    with pytest.raises(ConfigError):
        read_secret_source(True, None)
