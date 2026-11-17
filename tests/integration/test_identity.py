from __future__ import annotations

import os

import pytest

from .harness import (
    EchoHandler,
    SharedInstance,
    TCPServer,
    UDPServer,
    client_command,
    client_identity,
    echo_roundtrip,
    free_port,
    free_udp_port,
    start_client,
    start_server,
    udp_roundtrip,
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


def probe(instance, server_hash, service, identity, password="secret"):
    args = [
        "probe",
        "--server",
        server_hash,
        "--service",
        service,
        "--identity",
        str(identity),
    ]
    if password is not None:
        args += ["--service-password", password]
    return client_command(instance, args)


def test_allowlisted_identity_forward(instance, echo, tmp_path):
    allowed_file = tmp_path / "allowed.id"
    other_file = tmp_path / "other.id"
    allowed_hash = client_identity(instance, allowed_file)
    other_hash = client_identity(instance, other_file)

    server, server_hash = start_server(
        instance,
        {"echo": echo.port},
        extra=["--allow-identity", f"echo:{allowed_hash}"],
    )
    local_port = free_port()
    client = None
    try:
        client = start_client(
            instance,
            server_hash,
            [f"echo:{local_port}:secret"],
            extra=["--identity", str(allowed_file)],
        )
        payload = os.urandom(1024)
        assert echo_roundtrip(local_port, payload) == payload

        allowed = probe(instance, server_hash, "echo", allowed_file)
        assert allowed.returncode == 0, allowed.stderr

        other = probe(instance, server_hash, "echo", other_file)
        assert other.returncode == 5, other.stderr

        anonymous = client_command(
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
        assert anonymous.returncode == 5, anonymous.stderr
        assert "authentication failed" in other.stderr
        assert "authentication failed" in anonymous.stderr
        for output in (other.stdout, other.stderr, anonymous.stdout, anonymous.stderr):
            assert allowed_hash not in output
            assert other_hash not in output

        listed = client_command(
            instance,
            [
                "list",
                "--server",
                server_hash,
                "--list-password",
                "listpass",
                "--identity",
                str(other_file),
            ],
        )
        assert listed.returncode == 0, listed.stderr
        assert "echo" in listed.stdout
        assert "yes" in listed.stdout
        assert allowed_hash not in listed.stdout
        assert other_hash not in listed.stdout
        assert allowed_hash not in listed.stderr
        assert other_hash not in listed.stderr
    finally:
        if client is not None:
            assert client.terminate() == 0
        assert server.terminate() == 0


def test_allowlisted_public_service(instance, echo, tmp_path):
    allowed_file = tmp_path / "allowed.id"
    other_file = tmp_path / "other.id"
    allowed_hash = client_identity(instance, allowed_file)
    client_identity(instance, other_file)

    server, server_hash = start_server(
        instance,
        {},
        public_services={"web": echo.port},
        extra=["--allow-identity", f"web:{allowed_hash}"],
    )
    local_port = free_port()
    client = None
    try:
        client = start_client(
            instance,
            server_hash,
            [],
            public_forwards=[f"web:{local_port}"],
            extra=["--identity", str(allowed_file)],
        )
        assert echo_roundtrip(local_port, b"public with identity") == b"public with identity"

        allowed = probe(instance, server_hash, "web", allowed_file, password=None)
        assert allowed.returncode == 0, allowed.stderr
        assert "auth=yes" in allowed.stdout

        other = probe(instance, server_hash, "web", other_file, password=None)
        assert other.returncode == 5, other.stderr

        listed = client_command(
            instance,
            ["list", "--server", server_hash, "--list-password", "listpass"],
        )
        assert listed.returncode == 0, listed.stderr
        assert "web" in listed.stdout
        assert "yes" in listed.stdout
    finally:
        if client is not None:
            assert client.terminate() == 0
        assert server.terminate() == 0


def test_allowlisted_udp_service(instance, udp_echo, tmp_path):
    allowed_file = tmp_path / "allowed.id"
    other_file = tmp_path / "other.id"
    allowed_hash = client_identity(instance, allowed_file)
    client_identity(instance, other_file)

    server, server_hash = start_server(
        instance,
        {},
        udp_services={"dns": udp_echo.port},
        extra=["--allow-identity", f"dns:{allowed_hash}"],
    )
    local_port = free_udp_port()
    client = None
    try:
        client = start_client(
            instance,
            server_hash,
            [],
            udp_forwards=[f"dns:{local_port}:secret"],
            extra=["--identity", str(allowed_file)],
        )
        payload = os.urandom(400)
        assert udp_roundtrip(local_port, payload) == payload

        rejected = client_command(
            instance,
            [
                "forward",
                "--server",
                server_hash,
                "--forward-udp",
                f"dns:{free_udp_port()}:secret",
                "--identity",
                str(other_file),
            ],
        )
        assert rejected.returncode == 5, rejected.stderr
    finally:
        if client is not None:
            assert client.terminate() == 0
        assert server.terminate() == 0
