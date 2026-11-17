from __future__ import annotations

from rns_portfwd.cli_client import build_parser as build_client_parser
from rns_portfwd.cli_client import main as client_main
from rns_portfwd.cli_server import build_parser as build_server_parser
from rns_portfwd.cli_server import main as server_main
from rns_portfwd.constants import (
    DEFAULT_MAX_CONNECTIONS_PER_PUBLIC,
    DEFAULT_UDP_IDLE_TIMEOUT,
    EXIT_CONFIG,
)


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
