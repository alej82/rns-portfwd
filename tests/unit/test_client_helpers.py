from __future__ import annotations

import logging
import socket

import pytest

from rns_portfwd.client import PortfwdClient, link_request
from rns_portfwd.config import Forward
from rns_portfwd.errors import BindError

LOGGER = logging.getLogger("test.client")


class FakeReceipt:
    def __init__(self, response=None):
        self.response = response


class FakeLink:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.calls = []

    def request(self, path, data=None, response_callback=None, failed_callback=None, timeout=None):
        self.calls.append((path, data))
        if self.mode == "ok":
            assert response_callback is not None
            response_callback(FakeReceipt({"ok": True}))
        elif self.mode == "failed":
            assert failed_callback is not None
            failed_callback(FakeReceipt())
        return True


class FakeMux:
    def __init__(self):
        self.closed_reason = None

    def close_all(self, reason, send_close=False):
        self.closed_reason = reason


def make_client(port=0):
    return PortfwdClient(
        server_hash=b"0" * 16,
        forwards=[Forward("echo", port, "pw")],
        bind="127.0.0.1",
        logger=LOGGER,
    )


def test_link_request_success():
    link = FakeLink()
    assert link_request(link, "/probe", {"x": 1}, 1) == {"ok": True}
    assert link.calls == [("/probe", {"x": 1})]


def test_link_request_failure():
    link = FakeLink(mode="failed")
    from rns_portfwd.errors import ResolutionError

    with pytest.raises(ResolutionError):
        link_request(link, "/probe", {}, 1)


def test_create_listeners_and_close():
    client = make_client(0)
    client._create_listeners()
    assert len(client.listeners) == 1
    forward, listener, port = client.listeners[0]
    assert forward.name == "echo"
    assert port > 0
    listener.close()


def test_create_udp_listener_and_close():
    client = PortfwdClient(
        server_hash=b"0" * 16,
        forwards=[Forward("dns", 0, "pw", protocol="udp")],
        bind="127.0.0.1",
        logger=LOGGER,
    )
    client._create_listeners()
    forward, listener, port = client.listeners[0]
    assert forward.protocol == "udp"
    assert listener.type == socket.SOCK_DGRAM
    assert port > 0
    listener.close()


def test_probe_all_rejects_protocol_mismatch(monkeypatch):
    from rns_portfwd.errors import AuthError

    def fake_request(link, path, data, timeout):
        return {"ok": True, "service": {"name": "echo", "protocol": "tcp"}}

    monkeypatch.setattr("rns_portfwd.client.link_request", fake_request)
    client = PortfwdClient(
        server_hash=b"0" * 16,
        forwards=[Forward("echo", 0, "pw", protocol="udp")],
        bind="127.0.0.1",
        logger=LOGGER,
    )
    client.link = object()
    with pytest.raises(AuthError):
        client._probe_all()


def test_probe_all_accepts_matching_protocol(monkeypatch):
    def fake_request(link, path, data, timeout):
        return {"ok": True, "service": {"name": "dns", "protocol": "udp"}}

    monkeypatch.setattr("rns_portfwd.client.link_request", fake_request)
    client = PortfwdClient(
        server_hash=b"0" * 16,
        forwards=[Forward("dns", 0, "pw", protocol="udp")],
        bind="127.0.0.1",
        logger=LOGGER,
    )
    client.link = object()
    client._probe_all()


def test_bind_collision_raises():
    occupied = socket.socket()
    occupied.bind(("127.0.0.1", 0))
    occupied.listen(1)
    port = occupied.getsockname()[1]
    try:
        client = make_client(port)
        with pytest.raises(BindError):
            client._create_listeners()
    finally:
        occupied.close()


def test_link_closed_clears_ready_and_closes_mux():
    client = make_client(0)
    mux = FakeMux()
    link = object()
    client.link = link
    client.mux = mux  # type: ignore[assignment]
    client._link_ready.set()
    client._handle_link_closed(link)
    assert client._link_ready.is_set() is False
    assert mux.closed_reason == "link lost"
    assert client.mux is None
