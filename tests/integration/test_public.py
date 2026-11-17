from __future__ import annotations

import os
import socket
import sys

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
def echo():
    server = TCPServer(EchoHandler)
    yield server
    server.close()


@pytest.fixture
def udp_echo():
    server = UDPServer()
    yield server
    server.close()


def _forward_lines(process: Process) -> list[str]:
    return [line for line in process.lines if " -> " in line]


def test_public_tcp_forward_roundtrip(instance, echo):
    server, server_hash = start_server(
        instance, {}, public_services={"web": echo.port}
    )
    client = None
    try:
        client = start_client(instance, server_hash, [], public_forwards=["web:0"])
        lines = _forward_lines(client)
        assert lines
        port = local_port_from_line(lines[-1])

        payload = b"public tcp " + os.urandom(512)
        assert echo_roundtrip(port, payload) == payload
    finally:
        if client is not None:
            assert client.terminate() == 0
        assert server.terminate() == 0


def test_public_udp_forward_roundtrip(instance, udp_echo):
    server, server_hash = start_server(
        instance, {}, public_udp_services={"dns": udp_echo.port}
    )
    local_port = free_udp_port()
    client = start_client(
        instance, server_hash, [], public_udp_forwards=[f"dns:{local_port}"]
    )
    try:
        payload = os.urandom(400)
        assert udp_roundtrip(local_port, payload) == payload
    finally:
        assert client.terminate() == 0
        assert server.terminate() == 0


def test_public_and_private_services_mix(instance, echo):
    server, server_hash = start_server(
        instance, {"ssh": echo.port}, public_services={"web": echo.port}
    )
    private_port = free_port()
    public_port = free_port()
    client = start_client(
        instance,
        server_hash,
        [f"ssh:{private_port}:secret"],
        public_forwards=[f"web:{public_port}"],
    )
    try:
        assert echo_roundtrip(private_port, b"private") == b"private"
        assert echo_roundtrip(public_port, b"public") == b"public"

        exit_codes: list[int] = []
        failure_lines: list[str] = []
        for service, password in (("ssh", "wrong"), ("missing", "secret")):
            port = free_port()
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
                    "--forward",
                    f"{service}:{port}:{password}",
                ]
            )
            try:
                process.wait_for("authentication failed", timeout=30)
                exit_codes.append(process.proc.wait(timeout=20))
                failure_lines.append(
                    next(line for line in process.lines if "authentication failed" in line)
                )
                probe = socket.socket()
                try:
                    assert probe.connect_ex(("127.0.0.1", port)) != 0
                finally:
                    probe.close()
            finally:
                process.terminate()

        assert exit_codes == [5, 5]
        # The server must not reveal whether the service exists or the password
        # was wrong: the client-visible failure differs only in the name asked for.
        assert failure_lines[0].replace("'ssh'", "'<service>'") == failure_lines[1].replace(
            "'missing'", "'<service>'"
        )
    finally:
        assert client.terminate() == 0
        assert server.terminate() == 0


def test_public_connection_limit_is_independent(instance, echo):
    server, server_hash = start_server(
        instance,
        {"ssh": echo.port},
        public_services={"web": echo.port},
        extra=["--max-connections-per-public", "1"],
    )
    public_port = free_port()
    private_port = free_port()
    client = start_client(
        instance,
        server_hash,
        [f"ssh:{private_port}:secret"],
        public_forwards=[f"web:{public_port}"],
    )
    first = None
    second = None
    try:
        first = socket.create_connection(("127.0.0.1", public_port), timeout=10)
        first.settimeout(20)
        first.sendall(b"first")
        assert first.recv(5) == b"first"

        second = socket.create_connection(("127.0.0.1", public_port), timeout=10)
        second.settimeout(20)
        second.sendall(b"second")
        try:
            remaining = second.recv(1)
        except ConnectionResetError:
            remaining = b""
        assert remaining == b""
        assert wait_until(
            lambda: "at its connection limit" in "\n".join(client.lines), timeout=20
        )
        assert any(
            "service web reached its connection limit" in line for line in server.lines
        )

        first.sendall(b"still works")
        assert first.recv(11) == b"still works"

        # The private service keeps its own limit and is unaffected.
        assert echo_roundtrip(private_port, b"private while public full") == (
            b"private while public full"
        )
    finally:
        for sock in (first, second):
            if sock is not None:
                sock.close()
        assert client.terminate() == 0
        assert server.terminate() == 0


def test_list_and_probe_report_public_auth(instance, echo):
    server, server_hash = start_server(
        instance, {"ssh": echo.port}, public_services={"web": echo.port}
    )
    try:
        listed = client_command(
            instance, ["list", "--server", server_hash, "--list-password", "listpass"]
        )
        assert listed.returncode == 0, listed.stderr
        rows = {}
        for line in listed.stdout.splitlines():
            fields = line.split()
            if len(fields) == 3 and fields[0] in {"ssh", "web"}:
                rows[fields[0]] = fields
        assert rows["web"] == ["web", "tcp", "no"]
        assert rows["ssh"] == ["ssh", "tcp", "yes"]

        probe = client_command(
            instance, ["probe", "--server", server_hash, "--service", "web"]
        )
        assert probe.returncode == 0, probe.stderr
        assert "auth=no" in probe.stdout

        denied = client_command(
            instance, ["probe", "--server", server_hash, "--service", "ssh"]
        )
        assert denied.returncode == 5
    finally:
        assert server.terminate() == 0


def test_announce_with_public_service_starts(instance, echo):
    server, server_hash = start_server(
        instance,
        {},
        public_services={"web": echo.port},
        extra=["--announce"],
    )
    client = None
    try:
        client = start_client(instance, server_hash, [], public_forwards=["web:0"])
        lines = _forward_lines(client)
        assert lines
        port = local_port_from_line(lines[-1])
        assert echo_roundtrip(port, b"announce ok") == b"announce ok"
    finally:
        if client is not None:
            assert client.terminate() == 0
        assert server.terminate() == 0
