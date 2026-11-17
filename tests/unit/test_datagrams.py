from __future__ import annotations

import logging
import socket
import threading
import time

from rns_portfwd.datagrams import DatagramSession

LOGGER = logging.getLogger("test.datagrams")


class FakeTransport:
    def __init__(self, max_datagram=1024):
        self._max = max_datagram
        self.sent = []
        self.closed = []
        self.windows = []
        self.lock = threading.Lock()

    def max_datagram(self):
        return self._max

    def send_datagram(self, stream_id, data):
        with self.lock:
            self.sent.append((stream_id, data))

    def send_window(self, stream_id, credit):
        with self.lock:
            self.windows.append((stream_id, credit))

    def send_close(self, stream_id, reason):
        with self.lock:
            self.closed.append((stream_id, reason))

    def datagrams(self):
        with self.lock:
            return [item[1] for item in self.sent]


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def make_udp_pair():
    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.bind(("127.0.0.1", 0))
    local = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    local.connect(peer.getsockname())
    return local, peer


def test_datagram_session_bridges_both_directions():
    local, peer = make_udp_pair()
    transport = FakeTransport()
    closed = threading.Event()
    session = DatagramSession(
        1,
        local,
        transport,
        window=4096,
        logger=LOGGER,
        on_closed=lambda s: closed.set(),
        owns_socket=True,
    )
    session.on_open_ok(4096)
    session.start()
    try:
        peer.sendto(b"from-local", local.getsockname())
        assert wait_for(lambda: transport.datagrams() == [b"from-local"])

        session.on_remote_datagram(b"from-peer")
        assert peer.recv(100) == b"from-peer"

        session.abort("test cleanup", send_close=False)
        assert wait_for(closed.is_set)
    finally:
        session.abort("test cleanup", send_close=False)
        local.close()
        peer.close()


def test_datagram_session_drops_oversized_local_datagrams():
    local, peer = make_udp_pair()
    transport = FakeTransport(max_datagram=10)
    session = DatagramSession(
        2,
        local,
        transport,
        window=4096,
        logger=LOGGER,
        owns_socket=True,
    )
    session.on_open_ok(4096)
    try:
        assert session.send_local(b"x" * 11) is False
        assert session.dropped_oversized == 1
        assert session.send_local(b"x" * 10) is True
    finally:
        session.abort("test cleanup", send_close=False)
        local.close()
        peer.close()


def test_datagram_session_drops_when_queue_is_full():
    local, peer = make_udp_pair()
    transport = FakeTransport()
    session = DatagramSession(
        3,
        local,
        transport,
        window=4096,
        logger=LOGGER,
        owns_socket=True,
        queue_size=1,
    )
    session.on_open_ok(4096)
    try:
        assert session.send_local(b"first") is True
        assert session.send_local(b"second") is False
        assert session.dropped_queue_full == 1
    finally:
        session.abort("test cleanup", send_close=False)
        local.close()
        peer.close()


def test_datagram_session_reports_idle_time():
    local, peer = make_udp_pair()
    session = DatagramSession(
        4,
        local,
        FakeTransport(),
        window=4096,
        logger=LOGGER,
        owns_socket=True,
        idle_timeout=10,
    )
    try:
        assert session.idle_for() < 1
        session._last_activity = time.monotonic() - 20
        assert session.idle_for() >= 10
    finally:
        session.abort("test cleanup", send_close=False)
        local.close()
        peer.close()


def test_datagram_session_uses_peer_window_for_size_limit():
    local, peer = make_udp_pair()
    transport = FakeTransport(max_datagram=1400)
    session = DatagramSession(
        5,
        local,
        transport,
        window=4096,
        logger=LOGGER,
        owns_socket=True,
    )
    try:
        assert session.max_datagram() == 1400
        session.on_open_ok(512)
        assert session.max_datagram() == 512
        assert session.send_local(b"x" * 600) is False
        assert session.dropped_oversized == 1
    finally:
        session.abort("test cleanup", send_close=False)
        local.close()
        peer.close()
