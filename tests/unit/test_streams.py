from __future__ import annotations

import bz2
import logging
import os
import socket
import threading
import time

import pytest

from rns_portfwd.errors import ProtocolError
from rns_portfwd.protocol import DataMessage
from rns_portfwd.streams import ChannelTransport, StreamSession, decompress_payload

LOGGER = logging.getLogger("test.streams")


class FakeChannel:
    def __init__(self, mdu=431):
        self.mdu = mdu
        self.sent = []

    def send(self, message):
        self.sent.append(message)


class FakeTransport:
    def __init__(self, max_payload=1024):
        self._max = max_payload
        self.sent = []
        self.closed = []
        self.lock = threading.Lock()

    def max_payload(self):
        return self._max

    def send_data(self, stream_id, data, eof=False):
        with self.lock:
            self.sent.append((stream_id, data, eof))

    def send_window(self, stream_id, credit):
        with self.lock:
            self.sent.append(("window", stream_id, credit))

    def send_close(self, stream_id, reason):
        with self.lock:
            self.closed.append((stream_id, reason))

    def data_bytes(self):
        with self.lock:
            return b"".join(item[1] for item in self.sent if item[0] != "window")


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_transport_sends_open_and_window():
    channel = FakeChannel()
    transport = ChannelTransport(channel, compress=False)
    transport.send_open(1, 1, "ssh", "pw")
    transport.send_open_ok(1, 1024)
    transport.send_open_err(1, 1)
    transport.send_window(1, 512)
    transport.send_close(1, 2)
    kinds = [type(m).__name__ for m in channel.sent]
    assert kinds == [
        "OpenMessage",
        "OpenOkMessage",
        "OpenErrMessage",
        "WindowMessage",
        "CloseMessage",
    ]
    assert channel.mdu - 3 == transport.max_payload()


def test_transport_compresses_only_when_smaller():
    channel = FakeChannel()
    transport = ChannelTransport(channel, compress=True)
    transport.send_data(1, b"a" * 200)
    compressed = channel.sent[-1]
    assert isinstance(compressed, DataMessage)
    assert compressed.compressed is True
    assert len(compressed.data) < 200

    payload = os.urandom(2000)
    transport.send_data(1, payload)
    raw = channel.sent[-1]
    assert raw.compressed is False
    assert raw.data == payload


def test_transport_can_disable_compression():
    channel = FakeChannel()
    transport = ChannelTransport(channel, compress=False)
    transport.send_data(1, b"a" * 200)
    assert channel.sent[-1].compressed is False


def test_decompress_payload():
    original = b"hello" * 100
    assert decompress_payload(bz2.compress(original)) == original
    with pytest.raises(ProtocolError):
        decompress_payload(b"not bz2")
    bomb = bz2.compress(b"\0" * (2 * 1024 * 1024))
    with pytest.raises(ProtocolError):
        decompress_payload(bomb, limit=1024 * 1024)


def test_session_bridges_both_directions_and_half_close():
    local, peer = socket.socketpair()
    transport = FakeTransport()
    closed = threading.Event()
    session = StreamSession(
        1,
        local,
        transport,
        window=4096,
        logger=LOGGER,
        on_closed=lambda s: closed.set(),
    )
    session.on_open_ok(4096)
    session.start()
    try:
        peer.sendall(b"from-local")
        assert wait_for(lambda: b"from-local" in transport.data_bytes())

        session.on_remote_data(b"from-peer", eof=False, compressed=False)
        assert peer.recv(100) == b"from-peer"

        session.on_remote_data(bz2.compress(b"compressed"), eof=False, compressed=True)
        assert peer.recv(100) == b"compressed"

        session.on_remote_data(b"", eof=True, compressed=False)
        assert peer.recv(100) == b""

        peer.shutdown(socket.SHUT_WR)
        assert wait_for(closed.is_set)
        assert session.closed is True
    finally:
        session.abort("test cleanup", send_close=False)
        local.close()
        peer.close()


def test_session_closes_on_corrupt_payload():
    local, peer = socket.socketpair()
    transport = FakeTransport()
    closed = threading.Event()
    session = StreamSession(
        2,
        local,
        transport,
        window=1024,
        logger=LOGGER,
        on_closed=lambda s: closed.set(),
    )
    session.on_open_ok(1024)
    session.start()
    try:
        session.on_remote_data(b"not compressed", eof=False, compressed=True)
        assert wait_for(closed.is_set)
        assert session.closed is True
    finally:
        session.abort("test cleanup", send_close=False)
        local.close()
        peer.close()


def test_per_stream_credit_isolates_a_slow_stream():
    def make(window):
        local, peer = socket.socketpair()
        transport = FakeTransport(max_payload=1024)
        session = StreamSession(
            1,
            local,
            transport,
            window=window,
            logger=LOGGER,
        )
        session.on_open_ok(window)
        session.start()
        return session, peer, transport

    slow, slow_peer, slow_transport = make(8)
    fast, fast_peer, fast_transport = make(4096)
    try:
        slow_peer.sendall(b"A" * 100)
        fast_peer.sendall(b"B" * 100)
        assert wait_for(lambda: len(fast_transport.data_bytes()) == 100)
        time.sleep(0.2)
        assert len(slow_transport.data_bytes()) == 8
        slow.on_window(50)
        assert wait_for(lambda: len(slow_transport.data_bytes()) == 58)
    finally:
        slow.abort("cleanup", send_close=False)
        fast.abort("cleanup", send_close=False)
        slow_peer.close()
        fast_peer.close()
