from __future__ import annotations

import logging
import socket

import pytest

from rns_portfwd.client import PortfwdClient, link_request, probe_request
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


def test_probe_request_retries_once_after_identifying(monkeypatch):
    responses = [
        {"ok": False, "error": "auth"},
        {"ok": True, "service": {"name": "ssh", "protocol": "tcp"}},
    ]

    def fake_request(link, path, data, timeout):
        return responses.pop(0)

    class FakeIdentityLink:
        def __init__(self):
            self.identified = []

        def identify(self, identity):
            self.identified.append(identity)

    monkeypatch.setattr("rns_portfwd.client.link_request", fake_request)
    monkeypatch.setattr("rns_portfwd.client.time.sleep", lambda seconds: None)
    link = FakeIdentityLink()
    identity = object()

    response = probe_request(link, "ssh", "pw", 1, identity)

    assert response["ok"] is True
    assert link.identified == [identity]
    assert responses == []


def test_probe_request_does_not_retry_without_identity(monkeypatch):
    calls = []

    def fake_request(link, path, data, timeout):
        calls.append(path)
        return {"ok": False, "error": "auth"}

    monkeypatch.setattr("rns_portfwd.client.link_request", fake_request)
    assert probe_request(object(), "ssh", "pw", 1, None) == {
        "ok": False,
        "error": "auth",
    }
    assert calls == ["/probe"]


def test_probe_all_sends_empty_password_for_public_forward(monkeypatch):
    seen: list[dict] = []
    def fake_request(link, path, data, timeout):
        seen.append(dict(data))
        return {"ok": True, "service": {"name": "web", "protocol": "tcp"}}

    monkeypatch.setattr("rns_portfwd.client.link_request", fake_request)
    client = PortfwdClient(
        server_hash=b"0" * 16,
        forwards=[Forward("web", 0, None)],
        bind="127.0.0.1",
        logger=LOGGER,
    )
    client.link = object()
    client._probe_all()
    assert seen == [{"service": "web", "password": ""}]


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


def test_client_stores_client_identity():
    client_identity = object()
    client = PortfwdClient(
        server_hash=b"0" * 16,
        forwards=[Forward("echo", 0, "pw")],
        bind="127.0.0.1",
        logger=LOGGER,
        client_identity=client_identity,
    )
    assert client.client_identity is client_identity


def test_client_default_is_anonymous():
    assert make_client().client_identity is None


def test_client_default_link_stamp_settings():
    client = make_client()
    assert client.link_stamp_cost is None
    assert client.no_link_stamp is False


def test_client_stores_link_stamp_settings():
    client = PortfwdClient(
        server_hash=b"0" * 16,
        forwards=[Forward("echo", 0, "pw")],
        bind="127.0.0.1",
        logger=LOGGER,
        link_stamp_cost=12.5,
        no_link_stamp=False,
    )
    assert client.link_stamp_cost == 12.5
    assert client.no_link_stamp is False


class StopEvent:
    def is_set(self):
        return True

    def set(self):
        pass

    def wait(self, timeout=None):
        return True


def test_connect_once_configures_link_stamping_before_link(monkeypatch):
    order: list[str] = []
    configured: list[tuple] = []

    def fake_configure(server_hash, override, disabled, logger):
        order.append("stamp")
        configured.append((server_hash, override, disabled, logger))

    monkeypatch.setattr("rns_portfwd.client.configure_link_stamping", fake_configure)

    class OpenedLink:
        def get_channel(self):
            return object()

    class Manager:
        def __init__(self, identity, **kwargs):
            order.append("manager")

        def open(self):
            order.append("open")
            return OpenedLink()

    class MuxFactory:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("rns_portfwd.client.LinkManager", Manager)
    monkeypatch.setattr("rns_portfwd.client.Mux", MuxFactory)
    monkeypatch.setattr("rns_portfwd.client.register_message_types", lambda channel: None)

    client = PortfwdClient(
        server_hash=b"0" * 16,
        forwards=[],
        bind="127.0.0.1",
        logger=LOGGER,
        link_stamp_cost=8.0,
    )
    client._identity = object()

    client._connect_once()

    assert order == ["stamp", "manager", "open"]
    assert configured == [(b"0" * 16, 8.0, False, LOGGER)]



def test_connect_once_passes_client_identity_to_link_manager(monkeypatch):
    captured: dict = {}

    class Manager:
        def __init__(
            self,
            identity,
            timeout=None,
            on_closed=None,
            client_identity=None,
            logger=None,
        ):
            captured["server_identity"] = identity
            captured["timeout"] = timeout
            captured["on_closed"] = on_closed
            captured["client_identity"] = client_identity
            captured["logger"] = logger

        def open(self):
            return OpenedLink()

    class OpenedLink:
        def get_channel(self):
            return object()

    class MuxFactory:
        def __init__(self, **kwargs):
            captured["mux"] = kwargs

    monkeypatch.setattr("rns_portfwd.client.LinkManager", Manager)
    monkeypatch.setattr("rns_portfwd.client.Mux", MuxFactory)
    monkeypatch.setattr("rns_portfwd.client.register_message_types", lambda channel: None)

    client_identity = object()
    server_identity = object()
    client = PortfwdClient(
        server_hash=b"0" * 16,
        forwards=[Forward("echo", 0, "pw")],
        bind="127.0.0.1",
        logger=LOGGER,
        connect_timeout=7.0,
        client_identity=client_identity,
    )
    client._identity = server_identity
    client._connect_once()

    assert captured["server_identity"] is server_identity
    assert captured["client_identity"] is client_identity
    assert captured["logger"] is LOGGER
    assert captured["timeout"] == 7.0
    assert client.link_manager is not None
    assert client.mux is not None
