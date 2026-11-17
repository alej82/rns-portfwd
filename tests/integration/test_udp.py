from __future__ import annotations

import os
import socket
import sys
import time

import pytest

from .harness import (
    EchoHandler,
    Process,
    SharedInstance,
    TCPServer,
    UDPServer,
    client_command,
    echo_roundtrip,
    free_port,
    free_udp_port,
    local_port_from_line,
    start_client,
    start_server,
    udp_roundtrip,
    wait_until,
)


@pytest.fixture
def instance(tmp_path):
    shared = SharedInstance(tmp_path / "shared")
    shared.start()
    yield shared
    shared.stop()


@pytest.fixture
def udp_echo():
    server = UDPServer()
    yield server
    server.close()


@pytest.fixture
def echo():
    server = TCPServer(EchoHandler)
    yield server
    server.close()


def test_udp_list_probe_and_forward(instance, udp_echo):
    server, server_hash = start_server(
        instance, {}, udp_services={"dns": udp_echo.port}
    )
    local_port = free_udp_port()
    client = None
    try:
        listed = client_command(
            instance, ["list", "--server", server_hash, "--list-password", "listpass"]
        )
        assert listed.returncode == 0, listed.stderr
        assert "dns" in listed.stdout
        assert "udp" in listed.stdout

        probed = client_command(
            instance,
            ["probe", "--server", server_hash, "--service", "dns", "--service-password", "secret"],
        )
        assert probed.returncode == 0, probed.stderr
        assert "dns: ok (udp" in probed.stdout

        client = start_client(
            instance, server_hash, [], udp_forwards=[f"dns:{local_port}:secret"]
        )
        payload = os.urandom(400)
        assert udp_roundtrip(local_port, payload) == payload
    finally:
        if client is not None:
            assert client.terminate() == 0
        assert server.terminate() == 0


def test_udp_two_local_peers_are_isolated(instance, udp_echo):
    server, server_hash = start_server(
        instance, {}, udp_services={"dns": udp_echo.port}
    )
    local_port = free_udp_port()
    client = start_client(
        instance, server_hash, [], udp_forwards=[f"dns:{local_port}:secret"]
    )
    first = None
    second = None
    try:
        first = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        first.settimeout(15)
        first.connect(("127.0.0.1", local_port))
        second = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        second.settimeout(15)
        second.connect(("127.0.0.1", local_port))

        first.send(b"first-peer")
        second.send(b"second-peer")
        assert first.recv(100) == b"first-peer"
        assert second.recv(100) == b"second-peer"

        first.send(b"still-first")
        assert first.recv(100) == b"still-first"
    finally:
        for sock in (first, second):
            if sock is not None:
                sock.close()
        assert client.terminate() == 0
        assert server.terminate() == 0


def test_udp_ephemeral_local_port(instance, udp_echo):
    server, server_hash = start_server(
        instance, {}, udp_services={"dns": udp_echo.port}
    )
    client = start_client(
        instance, server_hash, [], udp_forwards=["dns:0:secret"]
    )
    try:
        lines = [line for line in client.lines if " -> " in line]
        assert lines
        port = local_port_from_line(lines[-1])
        assert port > 0
        assert udp_roundtrip(port, b"ephemeral") == b"ephemeral"
    finally:
        assert client.terminate() == 0
        assert server.terminate() == 0


def test_udp_oversized_datagram_is_dropped(instance, udp_echo):
    server, server_hash = start_server(
        instance,
        {},
        udp_services={"dns": udp_echo.port},
        extra=["--window", "4096"],
    )
    local_port = free_udp_port()
    client = start_client(
        instance,
        server_hash,
        [],
        udp_forwards=[f"dns:{local_port}:secret"],
        extra=["--window", "4096"],
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(3)
        sock.connect(("127.0.0.1", local_port))
        sock.send(b"x" * 8000)
        with pytest.raises(socket.timeout):
            sock.recv(65536)
        sock.send(b"small")
        assert sock.recv(100) == b"small"
    finally:
        sock.close()
        assert client.terminate() == 0
        assert server.terminate() == 0


def test_udp_idle_timeout_recreates_the_session(instance, udp_echo):
    server, server_hash = start_server(
        instance,
        {},
        udp_services={"dns": udp_echo.port},
        extra=["--udp-idle-timeout", "1"],
    )
    local_port = free_udp_port()
    client = start_client(
        instance,
        server_hash,
        [],
        udp_forwards=[f"dns:{local_port}:secret"],
        extra=["--udp-idle-timeout", "1"],
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(15)
        sock.connect(("127.0.0.1", local_port))
        sock.send(b"first")
        assert sock.recv(100) == b"first"
        time.sleep(2.5)
        sock.send(b"second")
        assert sock.recv(100) == b"second"
    finally:
        sock.close()
        assert client.terminate() == 0
        assert server.terminate() == 0


def test_tcp_and_udp_forwards_share_one_link(instance, echo, udp_echo):
    server, server_hash = start_server(
        instance, {"echo": echo.port}, udp_services={"dns": udp_echo.port}
    )
    tcp_port = free_port()
    udp_port = free_udp_port()
    client = start_client(
        instance,
        server_hash,
        [f"echo:{tcp_port}:secret"],
        udp_forwards=[f"dns:{udp_port}:secret"],
    )
    try:
        assert echo_roundtrip(tcp_port, b"tcp") == b"tcp"
        assert udp_roundtrip(udp_port, b"udp") == b"udp"
        assert "connected to" in "\n".join(client.lines)
    finally:
        assert client.terminate() == 0
        assert server.terminate() == 0


def test_udp_forward_to_tcp_service_exits_with_code_5(instance, echo):
    server, server_hash = start_server(instance, {"echo": echo.port})
    port = free_udp_port()
    process = Process(
        [
            sys.executable,
            "-m",
            "rns_portfwd.cli_client",
            "--config",
            str(instance.root),
            "forward",
            "--server",
            server_hash,
            "--forward-udp",
            f"echo:{port}:secret",
        ]
    )
    try:
        assert process.proc.wait(timeout=40) == 5
        assert "is tcp, not udp" in "\n".join(process.lines)
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.bind(("127.0.0.1", port))
        finally:
            probe.close()
    finally:
        process.terminate()
        assert server.terminate() == 0


def test_udp_only_client_keeps_link_past_auth_timeout(instance, udp_echo):
    server, server_hash = start_server(
        instance,
        {},
        udp_services={"dns": udp_echo.port},
        extra=["--auth-timeout", "2"],
    )
    local_port = free_udp_port()
    client = start_client(
        instance, server_hash, [], udp_forwards=[f"dns:{local_port}:secret"]
    )
    try:
        assert udp_roundtrip(local_port, b"before") == b"before"
        time.sleep(5)
        assert udp_roundtrip(local_port, b"after") == b"after"
        assert "no stream opened" not in "\n".join(server.lines)
    finally:
        assert client.terminate() == 0
        assert server.terminate() == 0


def test_udp_service_connection_limit(instance, udp_echo):
    server, server_hash = start_server(
        instance,
        {},
        udp_services={"dns": udp_echo.port},
        extra=["--max-connections-per-service", "1"],
    )
    local_port = free_udp_port()
    client = start_client(
        instance, server_hash, [], udp_forwards=[f"dns:{local_port}:secret"]
    )
    first = None
    second = None
    try:
        first = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        first.settimeout(15)
        first.connect(("127.0.0.1", local_port))
        first.send(b"first")
        assert first.recv(100) == b"first"

        second = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        second.settimeout(3)
        second.connect(("127.0.0.1", local_port))
        second.send(b"second")
        with pytest.raises(socket.timeout):
            second.recv(100)

        first.send(b"still-first")
        assert first.recv(100) == b"still-first"
    finally:
        for sock in (first, second):
            if sock is not None:
                sock.close()
        assert client.terminate() == 0
        assert server.terminate() == 0


@pytest.mark.slow
def test_udp_link_drop_and_reconnect(instance, udp_echo):
    server, server_hash = start_server(
        instance, {}, udp_services={"dns": udp_echo.port}
    )
    local_port = free_udp_port()
    client = start_client(
        instance, server_hash, [], udp_forwards=[f"dns:{local_port}:secret"]
    )
    try:
        assert udp_roundtrip(local_port, b"before") == b"before"
        assert server.terminate() == 0
        time.sleep(1.5)
        restarted, new_hash = start_server(
            instance, {}, udp_services={"dns": udp_echo.port}
        )
        assert new_hash == server_hash
        try:
            assert wait_until(
                lambda: udp_roundtrip(local_port, b"after", timeout=5) == b"after",
                timeout=45,
                interval=1.0,
            )
        finally:
            assert restarted.terminate() == 0
    finally:
        assert client.terminate() == 0
