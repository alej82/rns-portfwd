from __future__ import annotations

import logging
import time

from rns_portfwd.auth import AuthTracker
from rns_portfwd.config import Service
from rns_portfwd.constants import ErrorCode
from rns_portfwd.protocol import OpenMessage
from rns_portfwd.server import LinkContext, PortfwdServer

LOGGER = logging.getLogger("test.server")


class FakeTransport:
    def __init__(self):
        self.errors = []

    def send_open_err(self, stream_id, code):
        self.errors.append((stream_id, code))


class FakeMux:
    def __init__(self, reserve=True):
        self.transport = FakeTransport()
        self.reserve_result = reserve

    def reserve(self, stream_id):
        return self.reserve_result

    def release_reservation(self, stream_id):
        pass


class FakeLink:
    def __init__(self):
        self.torn_down = False

    def teardown(self):
        self.torn_down = True


def make_server(list_password: str | None = "listpass", max_connections=2):
    return PortfwdServer(
        services=[Service("127.0.0.1", 22, "ssh", "secret")],
        list_password=list_password,
        identity_path="/tmp/unused.id",
        name="test",
        config_dir=None,
        logger=LOGGER,
        max_connections_per_service=max_connections,
    )


def make_ctx(server, reserve=True):
    mux = FakeMux(reserve=reserve)
    link = FakeLink()
    ctx = LinkContext(link, None, mux, AuthTracker(max_failures=3))
    return ctx, link, mux


def args(data):
    return ("/list", data, b"id", b"link", None, time.time())


def test_list_requires_password(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    server = make_server()
    assert server._handle_list(*args({"password": "listpass"})) == {
        "ok": True,
        "services": [{"name": "ssh", "protocol": "tcp", "auth": True}],
    }
    assert server._handle_list(*args({"password": "wrong"})) == {
        "ok": False,
        "error": "auth",
    }
    assert server._handle_list(*args({})) == {"ok": False, "error": "auth"}


def test_list_disabled():
    server = make_server(list_password=None)
    assert server._handle_list(*args({"password": "anything"})) == {
        "ok": False,
        "error": "disabled",
    }


def test_probe_success_and_uniform_failure(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    server = make_server()
    assert server._handle_probe(*args({"service": "ssh", "password": "secret"})) == {
        "ok": True,
        "service": {"name": "ssh", "protocol": "tcp", "auth": True},
    }
    wrong = server._handle_probe(*args({"service": "ssh", "password": "bad"}))
    missing = server._handle_probe(*args({"service": "nope", "password": "bad"}))
    assert wrong == missing == {"ok": False, "error": "auth"}


def test_open_auth_failure_is_delayed_and_counted(monkeypatch):
    server = make_server()
    ctx, link, mux = make_ctx(server)
    for stream_id in range(1, 4):
        server._handle_open(ctx, OpenMessage(stream_id, 1, "ssh", "wrong"))
    time.sleep(0.6)
    assert mux.transport.errors == [
        (1, ErrorCode.AUTH),
        (2, ErrorCode.AUTH),
        (3, ErrorCode.AUTH),
    ]
    assert link.torn_down is True


def test_open_version_and_limit_errors():
    server = make_server(max_connections=1)
    ctx, link, mux = make_ctx(server)
    server._handle_open(ctx, OpenMessage(1, 99, "ssh", "secret"))
    assert mux.transport.errors[-1] == (1, ErrorCode.VERSION)

    assert server.registry.acquire("ssh") is True
    server._handle_open(ctx, OpenMessage(2, 1, "ssh", "secret"))
    assert mux.transport.errors[-1] == (2, ErrorCode.LIMIT)
    server.registry.release("ssh")


def test_open_stream_limit():
    server = make_server()
    ctx, link, mux = make_ctx(server, reserve=False)
    server._handle_open(ctx, OpenMessage(1, 1, "ssh", "secret"))
    assert mux.transport.errors == [(1, ErrorCode.LIMIT)]
