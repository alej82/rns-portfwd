from __future__ import annotations

import io
import json
import sys

from rns_portfwd.cli_client import build_parser as build_client_parser
from rns_portfwd.cli_client import main as client_main
from rns_portfwd.cli_server import build_parser as build_server_parser
from rns_portfwd.cli_server import main as server_main
from rns_portfwd.constants import (
    DEFAULT_MAX_CONNECTIONS_PER_PUBLIC,
    DEFAULT_UDP_IDLE_TIMEOUT,
    EXIT_CONFIG,
    EXIT_OK,
)

HASH = "0123456789abcdef0123456789abcdef"
OTHER_HASH = "fedcba9876543210fedcba9876543210"


def capture_server(monkeypatch):
    captured = {}

    class FakeServer:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        @property
        def service_names(self):
            return [service.name for service in captured["services"]]

        def start(self):
            return "0" * 32

        def wait(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr("rns_portfwd.cli_server.PortfwdServer", FakeServer)
    return captured


def test_server_parser_accepts_udp_services():
    args = build_server_parser().parse_args(
        [
            "--expose",
            "127.0.0.1:22:ssh:pw",
            "--expose-udp",
            "127.0.0.1:53:dns:pw",
            "--udp-idle-timeout",
            "5",
        ]
    )
    assert args.expose == ["127.0.0.1:22:ssh:pw"]
    assert args.expose_udp == ["127.0.0.1:53:dns:pw"]
    assert args.udp_idle_timeout == 5


def test_server_parser_defaults_udp_idle_timeout():
    args = build_server_parser().parse_args(["--expose-udp", "127.0.0.1:53:dns:pw"])
    assert args.udp_idle_timeout == DEFAULT_UDP_IDLE_TIMEOUT


def test_client_parser_accepts_udp_forwards():
    args = build_client_parser().parse_args(
        [
            "forward",
            "--server",
            "0" * 32,
            "--forward",
            "ssh:2222:pw",
            "--forward-udp",
            "dns:5353:pw",
            "--udp-idle-timeout",
            "5",
        ]
    )
    assert args.forward == ["ssh:2222:pw"]
    assert args.forward_udp == ["dns:5353:pw"]
    assert args.udp_idle_timeout == 5


def test_client_parser_defaults_udp_idle_timeout():
    args = build_client_parser().parse_args(
        ["forward", "--server", "0" * 32, "--forward-udp", "dns:5353:pw"]
    )
    assert args.udp_idle_timeout == DEFAULT_UDP_IDLE_TIMEOUT


def test_server_parser_accepts_public_services():
    args = build_server_parser().parse_args(
        [
            "--expose-public",
            "127.0.0.1:80:web",
            "--expose-udp-public",
            "127.0.0.1:53:dns",
        ]
    )
    assert args.expose_public == ["127.0.0.1:80:web"]
    assert args.expose_udp_public == ["127.0.0.1:53:dns"]
    assert args.max_connections_per_public == DEFAULT_MAX_CONNECTIONS_PER_PUBLIC


def test_server_rejects_zero_public_limit():
    assert (
        server_main(
            [
                "--expose-public",
                "127.0.0.1:80:web",
                "--max-connections-per-public",
                "0",
            ]
        )
        == EXIT_CONFIG
    )


def test_client_parser_accepts_public_forwards():
    args = build_client_parser().parse_args(
        [
            "forward",
            "--server",
            "0" * 32,
            "--forward-public",
            "web:8080",
            "--forward-udp-public",
            "dns:5353",
        ]
    )
    assert args.forward_public == ["web:8080"]
    assert args.forward_udp_public == ["dns:5353"]


def test_client_rejects_duplicate_public_port():
    assert (
        client_main(
            [
                "forward",
                "--server",
                "0" * 32,
                "--forward",
                "db:8080:pw",
                "--forward-public",
                "web:8080",
            ]
        )
        == EXIT_CONFIG
    )


HASH = "0123456789abcdef0123456789abcdef"


def test_server_rejects_short_auth_timeout_with_allow_identity():
    assert (
        server_main(
            [
                "--expose-public",
                "127.0.0.1:80:web",
                "--allow-identity",
                f"web:{HASH}",
                "--auth-timeout",
                "3",
            ]
        )
        == EXIT_CONFIG
    )


def test_server_validates_allow_identity_before_reading_secrets(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("secret source must not be read")

    monkeypatch.setattr("rns_portfwd.cli_server.read_secret_source", fail)
    assert (
        server_main(
            [
                "--expose-public",
                "127.0.0.1:80:web",
                "--allow-identity",
                f"nosuch:{HASH}",
                "--password-stdin",
            ]
        )
        == EXIT_CONFIG
    )


def test_server_parser_accepts_repeated_allow_identity():
    args = build_server_parser().parse_args(
        [
            "--expose",
            "127.0.0.1:22:ssh:pw",
            "--allow-identity",
            f"ssh:{HASH}",
            "--allow-identity",
            f"ssh:{OTHER_HASH}",
        ]
    )
    assert args.allow_identity == [f"ssh:{HASH}", f"ssh:{OTHER_HASH}"]
    assert build_server_parser().parse_args(
        ["--expose", "127.0.0.1:22:ssh:pw"]
    ).allow_identity == []


def test_server_applies_allow_identity(monkeypatch, capsys):
    captured = capture_server(monkeypatch)
    assert (
        server_main(
            [
                "--expose",
                "127.0.0.1:22:ssh:pw",
                "--allow-identity",
                f"ssh:{HASH}",
                "--allow-identity",
                f"ssh:{OTHER_HASH}",
            ]
        )
        == EXIT_OK
    )
    services = captured["services"]
    assert [service.name for service in services] == ["ssh"]
    assert services[0].allowed_identities == frozenset(
        {bytes.fromhex(HASH), bytes.fromhex(OTHER_HASH)}
    )
    assert services[0].password == "pw"
    out = capsys.readouterr().out
    assert HASH not in out.lower()
    assert OTHER_HASH not in out.lower()
    assert "services: ssh" in out


def test_server_applies_allow_identity_to_public_service(monkeypatch):
    captured = capture_server(monkeypatch)
    assert (
        server_main(
            [
                "--expose-public",
                "127.0.0.1:80:web",
                "--allow-identity",
                f"web:{HASH}",
            ]
        )
        == EXIT_OK
    )
    services = captured["services"]
    assert services[0].password is None
    assert services[0].allowed_identities == frozenset({bytes.fromhex(HASH)})
    assert services[0].public()["auth"] is True


def test_server_applies_allow_identity_after_secret_resolution(monkeypatch):
    captured = capture_server(monkeypatch)
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"services": {"ssh": "fromdoc"}}))
    )
    assert (
        server_main(
            [
                "--expose",
                "127.0.0.1:22:ssh:-",
                "--password-stdin",
                "--allow-identity",
                f"ssh:{HASH}",
            ]
        )
        == EXIT_OK
    )
    services = captured["services"]
    assert services[0].password == "fromdoc"
    assert services[0].allowed_identities == frozenset({bytes.fromhex(HASH)})


def test_server_rejects_malformed_allow_identity_hash():
    assert (
        server_main(
            [
                "--expose",
                "127.0.0.1:22:ssh:pw",
                "--allow-identity",
                "ssh:xyz",
            ]
        )
        == EXIT_CONFIG
    )
    assert (
        server_main(
            [
                "--expose",
                "127.0.0.1:22:ssh:pw",
                "--allow-identity",
                f"ssh:{HASH[:-2]}",
            ]
        )
        == EXIT_CONFIG
    )


def test_server_rejects_unknown_allow_identity_service():
    assert (
        server_main(
            [
                "--expose",
                "127.0.0.1:22:ssh:pw",
                "--allow-identity",
                f"nosuch:{HASH}",
            ]
        )
        == EXIT_CONFIG
    )
