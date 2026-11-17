from __future__ import annotations

from rns_portfwd.cli_client import build_parser as build_client_parser
from rns_portfwd.cli_server import build_parser as build_server_parser
from rns_portfwd.constants import DEFAULT_UDP_IDLE_TIMEOUT


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
