from __future__ import annotations

import os
import queue
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV = dict(os.environ, PYTHONPATH=str(ROOT / "src"))


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class SharedInstance:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.port = free_port()
        self.control_port = free_port()
        self.config = self.root / "config"
        self.config.write_text(
            "[reticulum]\n"
            "enable_transport = no\n"
            "share_instance = yes\n"
            "shared_instance_type = tcp\n"
            f"shared_instance_port = {self.port}\n"
            f"instance_control_port = {self.control_port}\n"
            "\n[logging]\n"
            "loglevel = 0\n"
        )
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "RNS.Utilities.rnsd", "--config", str(self.root)],
            env=ENV,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                sock = socket.create_connection(("127.0.0.1", self.port), timeout=0.2)
                sock.close()
                return
            except OSError:
                if self.proc.poll() is not None:
                    raise RuntimeError("rnsd exited during startup") from None
                time.sleep(0.1)
        raise RuntimeError("rnsd did not open its shared instance port")

    def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.proc = None


class EchoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            data = self.request.recv(65536)
            if not data:
                break
            self.request.sendall(data)
        try:
            self.request.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class BulkHandler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            for _ in range(64):
                self.request.sendall(b"B" * 65536)
        except OSError:
            pass


class TCPServer:
    def __init__(self, handler):
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Process:
    def __init__(self, args: list[str]):
        self.proc = subprocess.Popen(
            args,
            env=ENV,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self.lines: list[str] = []
        self.queue: queue.Queue = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            stripped = line.rstrip()
            self.lines.append(stripped)
            self.queue.put(stripped)
        self.queue.put(None)

    def wait_for(self, needle: str, timeout: float = 30.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.queue.get(timeout=0.2)
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise AssertionError(
                        f"process exited with {self.proc.returncode}: " + "\n".join(self.lines)
                    ) from None
                continue
            if line is None:
                raise AssertionError(
                    f"process exited while waiting for {needle!r}: " + "\n".join(self.lines)
                )
            if needle in line:
                return line
        raise AssertionError(f"timed out waiting for {needle!r}: " + "\n".join(self.lines))

    def terminate(self, timeout: float = 15.0) -> int:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return self.proc.wait(timeout=5)

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=5)


def start_server(
    instance: SharedInstance,
    services: dict[str, int],
    extra: list[str] | None = None,
    password: str = "secret",
    list_password: str = "listpass",
) -> tuple[Process, str]:
    identity = instance.root / "server.id"
    args = [
        sys.executable,
        "-m",
        "rns_portfwd.cli_server",
        "--config",
        str(instance.root),
        "--identity",
        str(identity),
        "--name",
        "integration",
        "--list-password",
        list_password,
    ]
    for name, port in services.items():
        args += ["--expose", f"127.0.0.1:{port}:{name}:{password}"]
    args += extra or []
    process = Process(args)
    line = process.wait_for("destination:", timeout=30)
    return process, line.split(":", 1)[1].strip()


def start_client(
    instance: SharedInstance,
    server_hash: str,
    forwards: list[str],
    extra: list[str] | None = None,
) -> Process:
    args = [
        sys.executable,
        "-m",
        "rns_portfwd.cli_client",
        "--config",
        str(instance.root),
        "forward",
        "--server",
        server_hash,
    ]
    for forward in forwards:
        args += ["--forward", forward]
    args += extra or []
    process = Process(args)
    for _ in forwards:
        process.wait_for(" -> ", timeout=30)
    process.wait_for("client ready", timeout=30)
    return process


def local_port_from_line(line: str) -> int:
    return int(line.rsplit(":", 1)[1].split()[0])


def client_command(instance: SharedInstance, args: list[str], timeout: float = 45) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "rns_portfwd.cli_client", "--config", str(instance.root)] + args,
        env=ENV,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def echo_roundtrip(port: int, payload: bytes, timeout: float = 30) -> bytes:
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks)
    finally:
        sock.close()


def wait_until(predicate, timeout: float = 20, interval: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False
