from __future__ import annotations

import os

import pytest

from .harness import (
    EchoHandler,
    SharedInstance,
    TCPServer,
    UDPServer,
    client_command,
    echo_roundtrip,
    free_port,
    free_udp_port,
    start_client,
    start_server,
    udp_roundtrip,
)

STAMP_COST = "8"
REJECT_COST = "20"


@pytest.fixture
def instance(tmp_path):
    shared = SharedInstance(tmp_path / "shared")
    shared.start()
    yield shared
    shared.stop()


@pytest.fixture
def echo():
    server = TCPServer(EchoHandler)
    yield server
    server.close()


@pytest.fixture
def udp_echo():
    server = UDPServer()
    yield server
    server.close()


def stamping_server(instance, cost=STAMP_COST, **kwargs):
    return start_server(instance, **kwargs, extra=["--link-stamp-cost", cost])


def test_stamped_tcp_forward_list_and_probe(instance, echo):
    server, server_hash = stamping_server(instance, services={"echo": echo.port})
    local_port = free_port()
    client = None
    try:
        client = start_client(
            instance, server_hash, [f"echo:{local_port}:secret"]
        )
        payload = os.urandom(1024)
        assert echo_roundtrip(local_port, payload) == payload

        listed = client_command(
            instance, ["list", "--server", server_hash, "--list-password", "listpass"]
        )
        assert listed.returncode == 0, listed.stderr
        assert "echo" in listed.stdout

        probed = client_command(
            instance,
            [
                "probe",
                "--server",
                server_hash,
                "--service",
                "echo",
                "--service-password",
                "secret",
            ],
        )
        assert probed.returncode == 0, probed.stderr
        assert "echo: ok" in probed.stdout
    finally:
        if client is not None:
            assert client.terminate() == 0
        assert server.terminate() == 0


def test_stamped_udp_forward(instance, udp_echo):
    server, server_hash = stamping_server(
        instance, services={}, udp_services={"dns": udp_echo.port}
    )
    local_port = free_udp_port()
    client = None
    try:
        client = start_client(
            instance, server_hash, [], udp_forwards=[f"dns:{local_port}:secret"]
        )
        payload = os.urandom(400)
        assert udp_roundtrip(local_port, payload) == payload
    finally:
        if client is not None:
            assert client.terminate() == 0
        assert server.terminate() == 0


def assert_link_rejected(instance, args):
    result = client_command(instance, args, timeout=30)
    assert result.returncode == 4, result.stderr
    assert "could not establish a Link" in result.stderr
    assert "secret" not in result.stderr
    return result


def test_client_without_stamping_cannot_connect(instance, echo):
    server, server_hash = stamping_server(
        instance, cost=REJECT_COST, services={"echo": echo.port}
    )
    try:
        local_port = free_port()
        assert_link_rejected(
            instance,
            [
                "forward",
                "--server",
                server_hash,
                "--forward",
                f"echo:{local_port}:secret",
                "--no-link-stamp",
                "--connect-timeout",
                "3",
            ],
        )
    finally:
        assert server.terminate() == 0


def test_client_with_lower_stamp_cost_cannot_connect(instance, echo):
    server, server_hash = stamping_server(
        instance, cost=REJECT_COST, services={"echo": echo.port}
    )
    try:
        local_port = free_port()
        assert_link_rejected(
            instance,
            [
                "forward",
                "--server",
                server_hash,
                "--forward",
                f"echo:{local_port}:secret",
                "--link-stamp-cost",
                "1",
                "--connect-timeout",
                "3",
            ],
        )
    finally:
        assert server.terminate() == 0
