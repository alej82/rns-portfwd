from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time

import pytest

from .harness import (
    ENV,
    BulkHandler,
    EchoHandler,
    Process,
    SharedInstance,
    TCPServer,
    client_command,
    echo_roundtrip,
    free_port,
    start_client,
    start_server,
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
def bulk():
    server = TCPServer(BulkHandler)
    yield server
    server.close()


def test_list_probe_and_forward(instance, echo):
    server, server_hash = start_server(instance, {"echo": echo.port})
    client = None
    try:
        listed = client_command(
            instance, ["list", "--server", server_hash, "--list-password", "listpass"]
        )
        assert listed.returncode == 0, listed.stderr
        assert "echo" in listed.stdout
        assert "secret" not in listed.stdout

        denied = client_command(
            instance, ["list", "--server", server_hash, "--list-password", "wrong"]
        )
        assert denied.returncode == 5

        probe = client_command(
            instance,
            ["probe", "--server", server_hash, "--service", "echo", "--service-password", "secret"],
        )
        assert probe.returncode == 0, probe.stderr
        assert "echo: ok" in probe.stdout

        bad_probe = client_command(
            instance,
            ["probe", "--server", server_hash, "--service", "echo", "--service-password", "wrong"],
        )
        assert bad_probe.returncode == 5

        local_port = free_port()
        client = start_client(
            instance, server_hash, [f"echo:{local_port}:secret"]
        )
        assert echo_roundtrip(local_port, b"hello over reticulum") == b"hello over reticulum"
    finally:
        if client is not None:
            assert client.terminate() == 0
        assert server.terminate() == 0


def test_authentication_and_unknown_service_fail_before_listening(instance, echo):
    server, server_hash = start_server(instance, {"echo": echo.port})
    try:
        for service, password in (("echo", "wrong"), ("missing", "secret")):
            local_port = free_port()
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
                    f"{service}:{local_port}:{password}",
                ]
            )
            try:
                process.wait_for("authentication failed", timeout=30)
                assert process.proc.wait(timeout=20) == 5
                probe = socket.socket()
                try:
                    assert probe.connect_ex(("127.0.0.1", local_port)) != 0
                finally:
                    probe.close()
            finally:
                process.terminate()
    finally:
        assert server.terminate() == 0


def test_concurrent_streams_half_close_and_large_transfer(instance, echo):
    server, server_hash = start_server(instance, {"echo": echo.port})
    local_port = free_port()
    client = start_client(instance, server_hash, [f"echo:{local_port}:secret"])
    try:
        payload = os.urandom(1024 * 1024)
        assert echo_roundtrip(local_port, payload) == payload

        errors: list[Exception] = []

        def worker(index):
            try:
                data = (f"worker-{index}-".encode() * 4096)
                assert echo_roundtrip(local_port, data) == data
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        assert errors == []
    finally:
        assert client.terminate() == 0
        assert server.terminate() == 0


def test_stream_limit_rejects_extra_connection(instance, echo):
    server, server_hash = start_server(
        instance, {"echo": echo.port}, extra=["--max-streams", "1"]
    )
    local_port = free_port()
    client = start_client(
        instance,
        server_hash,
        [f"echo:{local_port}:secret"],
        extra=["--max-streams", "1", "--connect-timeout", "2"],
    )
    first = None
    second = None
    try:
        first = socket.create_connection(("127.0.0.1", local_port), timeout=10)
        first.sendall(b"first")
        assert first.recv(5) == b"first"

        second = socket.create_connection(("127.0.0.1", local_port), timeout=10)
        second.sendall(b"second")
        second.settimeout(5)
        try:
            remaining = second.recv(6)
        except ConnectionResetError:
            remaining = b""
        assert remaining == b""

        first.sendall(b"still works")
        assert first.recv(11) == b"still works"
    finally:
        for sock in (first, second):
            if sock is not None:
                sock.close()
        assert client.terminate() == 0
        assert server.terminate() == 0


@pytest.mark.slow
def test_slow_stream_does_not_block_fast_stream(instance, echo, bulk):
    server, server_hash = start_server(
        instance, {"echo": echo.port, "bulk": bulk.port}
    )
    echo_port = free_port()
    bulk_port = free_port()
    client = start_client(
        instance,
        server_hash,
        [f"echo:{echo_port}:secret", f"bulk:{bulk_port}:secret"],
        extra=["--window", "4096"],
    )
    slow = None
    try:
        slow = socket.create_connection(("127.0.0.1", bulk_port), timeout=10)
        slow.settimeout(10)
        time.sleep(1.0)
        payload = os.urandom(100_000)
        assert echo_roundtrip(echo_port, payload, timeout=30) == payload
    finally:
        if slow is not None:
            slow.close()
        assert client.terminate() == 0
        assert server.terminate() == 0


def test_signal_shutdown(instance, echo):
    server, server_hash = start_server(instance, {"echo": echo.port})
    local_port = free_port()
    client = start_client(instance, server_hash, [f"echo:{local_port}:secret"])
    assert echo_roundtrip(local_port, b"before shutdown") == b"before shutdown"
    assert client.terminate() == 0
    assert server.terminate() == 0


@pytest.mark.slow
def test_link_drop_and_reconnect(instance, echo):
    server, server_hash = start_server(instance, {"echo": echo.port})
    local_port = free_port()
    client = start_client(instance, server_hash, [f"echo:{local_port}:secret"])
    try:
        assert echo_roundtrip(local_port, b"before") == b"before"
        assert server.terminate() == 0
        time.sleep(1.5)
        restarted, new_hash = start_server(instance, {"echo": echo.port})
        assert new_hash == server_hash
        try:
            assert wait_until(
                lambda: echo_roundtrip(local_port, b"after") == b"after",
                timeout=45,
                interval=1.0,
            )
        finally:
            assert restarted.terminate() == 0
    finally:
        assert client.terminate() == 0


def test_malformed_channel_messages_do_not_break_server(instance, echo):
    server, server_hash = start_server(instance, {"echo": echo.port})
    try:
        script = [
            sys.executable,
            "-m",
            "tests.integration.malformed_client",
            str(instance.root),
            server_hash,
        ]
        result = subprocess.run(
            script,
            env=ENV,
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert result.returncode == 0, result.stderr
        probe = client_command(
            instance,
            ["probe", "--server", server_hash, "--service", "echo", "--service-password", "secret"],
        )
        assert probe.returncode == 0, probe.stderr
    finally:
        assert server.terminate() == 0


def test_secrets_are_never_logged_or_printed(instance, echo):
    service_secret = "S3NTINEL_SERVICE_DO_NOT_LOG"
    list_secret = "S3NTINEL_LIST_DO_NOT_LOG"
    server, server_hash = start_server(
        instance,
        {"echo": echo.port},
        password=service_secret,
        list_password=list_secret,
    )
    local_port = free_port()
    client = start_client(
        instance, server_hash, [f"echo:{local_port}:{service_secret}"]
    )
    try:
        assert echo_roundtrip(local_port, b"secret audit") == b"secret audit"
        listed = client_command(
            instance, ["list", "--server", server_hash, "--list-password", list_secret]
        )
        probed = client_command(
            instance,
            [
                "probe",
                "--server",
                server_hash,
                "--service",
                "echo",
                "--service-password",
                service_secret,
            ],
        )
        assert listed.returncode == 0
        assert probed.returncode == 0
        combined = "\n".join(
            server.lines + client.lines + [listed.stdout, listed.stderr, probed.stdout, probed.stderr]
        )
        assert service_secret not in combined
        assert list_secret not in combined
    finally:
        assert client.terminate() == 0
        assert server.terminate() == 0


def test_local_bind_failure_exits_with_code_6(instance, echo):
    server, server_hash = start_server(instance, {"echo": echo.port})
    occupied = socket.socket()
    occupied.bind(("127.0.0.1", 0))
    occupied.listen(1)
    port = occupied.getsockname()[1]
    try:
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
                f"echo:{port}:secret",
            ]
        )
        assert process.proc.wait(timeout=40) == 6
    finally:
        occupied.close()
        assert server.terminate() == 0
