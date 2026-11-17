from __future__ import annotations

import logging
import time
from types import SimpleNamespace

from rns_portfwd.auth import AuthTracker
from rns_portfwd.config import Service
from rns_portfwd.constants import ErrorCode
from rns_portfwd.protocol import OpenMessage
from rns_portfwd.server import LinkContext, PortfwdServer

LOGGER = logging.getLogger("test.server")

IDENTITY_A = bytes.fromhex("0123456789abcdef0123456789abcdef")
IDENTITY_B = bytes.fromhex("fedcba9876543210fedcba9876543210")


class FakeTransport:
    def __init__(self):
        self.errors = []

    def send_open_err(self, stream_id, code):
        self.errors.append((stream_id, code))


class FakeMux:
    def __init__(self, reserve=True):
        self.transport = FakeTransport()
        self.reserve_result = reserve
        self.reserved = set()
        self.released = []
        self.closed = False

    def reserve(self, stream_id):
        if not self.reserve_result or stream_id in self.reserved:
            return False
        self.reserved.add(stream_id)
        return True

    def release_reservation(self, stream_id):
        self.released.append(stream_id)
        self.reserved.discard(stream_id)

    def close_all(self, reason, send_close=False):
        self.closed = True
        self.reserved.clear()


class FakeLink:
    def __init__(self):
        self.torn_down = False
        self.remote_identity = None

    def teardown(self):
        self.torn_down = True

    def get_remote_identity(self):
        return self.remote_identity


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


def make_ctx(server, reserve=True, identity=None, link=None):
    mux = FakeMux(reserve=reserve)
    link = link if link is not None else FakeLink()
    ctx = LinkContext(link, None, mux, AuthTracker(max_failures=3))
    ctx.identity_hash = identity
    return ctx, link, mux


def args(data, remote_identity=None):
    return ("/list", data, b"id", b"link", remote_identity, time.time())


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return predicate()


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


class FakeTimer:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


def test_successful_probe_or_list_marks_link_authenticated(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    server = make_server()
    link_id = b"link"
    ctx, _, _ = make_ctx(server)
    server._link_ids[link_id] = ctx
    ctx.timer = FakeTimer()

    server._handle_probe("/probe", {"service": "ssh", "password": "secret"}, b"id", link_id, None, time.time())
    assert ctx.opened is True
    assert ctx.timer.cancelled is True

    ctx.opened = False
    ctx.timer = FakeTimer()
    server._handle_probe("/probe", {"service": "ssh", "password": "bad"}, b"id", link_id, None, time.time())
    assert ctx.opened is False
    assert ctx.timer.cancelled is False

    server._handle_list("/list", {"password": "listpass"}, b"id", link_id, None, time.time())
    assert ctx.opened is True
    assert ctx.timer.cancelled is True


def test_open_auth_failure_is_delayed_and_counted(monkeypatch):
    server = make_server()
    ctx, link, mux = make_ctx(server)
    for stream_id in range(1, 4):
        server._handle_open(ctx, OpenMessage(stream_id, 1, "ssh", "wrong"))
    assert wait_for(lambda: len(mux.transport.errors) >= 3)
    assert sorted(mux.transport.errors) == [
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


def make_public_server(max_public=2):
    return PortfwdServer(
        services=[
            Service("127.0.0.1", 22, "ssh", "secret"),
            Service("127.0.0.1", 80, "web", None),
        ],
        list_password="listpass",
        identity_path="/tmp/unused.id",
        name="test",
        config_dir=None,
        logger=LOGGER,
        max_connections_per_service=2,
        max_connections_per_public=max_public,
    )


def test_open_public_service_skips_authentication(monkeypatch):
    server = make_public_server()
    ctx, link, mux = make_ctx(server)
    opened: list[int] = []
    monkeypatch.setattr(
        server, "_open_backend", lambda context, service, stream_id: opened.append(stream_id)
    )
    server._handle_open(ctx, OpenMessage(1, 1, "web", ""))
    server._handle_open(ctx, OpenMessage(2, 1, "web", "stray"))
    assert wait_for(lambda: len(opened) == 2)
    assert opened == [1, 2]
    assert ctx.tracker.failures == 0
    assert mux.transport.errors == []
    assert link.torn_down is False


def test_public_connection_limit_is_separate(monkeypatch):
    server = make_public_server(max_public=1)
    ctx, _, mux = make_ctx(server)
    monkeypatch.setattr(server, "_open_backend", lambda context, service, stream_id: None)
    server._handle_open(ctx, OpenMessage(1, 1, "web", ""))
    server._handle_open(ctx, OpenMessage(2, 1, "web", ""))
    assert mux.transport.errors == [(2, ErrorCode.LIMIT)]
    server._handle_open(ctx, OpenMessage(3, 1, "ssh", "secret"))
    assert mux.transport.errors == [(2, ErrorCode.LIMIT)]


def test_list_and_probe_report_public_auth(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    server = make_public_server()
    services = server._handle_list(*args({"password": "listpass"}))["services"]
    assert {item["name"]: item["auth"] for item in services} == {
        "ssh": True,
        "web": False,
    }
    assert server._handle_probe(*args({"service": "web", "password": ""})) == {
        "ok": True,
        "service": {"name": "web", "protocol": "tcp", "auth": False},
    }


# --- identity allow-lists -------------------------------------------------


def make_allowlist_server(identity_timeout=1.0, max_connections=2, max_public=1):
    return PortfwdServer(
        services=[
            Service(
                "127.0.0.1",
                22,
                "ssh",
                "secret",
                allowed_identities=frozenset({IDENTITY_A}),
            ),
            Service(
                "127.0.0.1",
                80,
                "web",
                None,
                allowed_identities=frozenset({IDENTITY_A}),
            ),
        ],
        list_password="listpass",
        identity_path="/tmp/unused.id",
        name="test",
        config_dir=None,
        logger=LOGGER,
        max_connections_per_service=max_connections,
        max_connections_per_public=max_public,
        identity_timeout=identity_timeout,
    )


class RacingLink(FakeLink):
    """First identity lookup returns None, later ones return the identity."""

    def __init__(self, identity):
        super().__init__()
        self._identity = identity
        self._calls = 0

    def get_remote_identity(self):
        self._calls += 1
        return None if self._calls == 1 else self._identity


class LateIdentityLink(FakeLink):
    """Identity becomes readable after a few lookups, with no callback fired."""

    def __init__(self, identity, hidden_lookups=2):
        super().__init__()
        self._identity = identity
        self._hidden = hidden_lookups
        self._calls = 0

    def get_remote_identity(self):
        self._calls += 1
        return None if self._calls <= self._hidden else self._identity


def test_open_with_known_identity_authorizes_immediately(monkeypatch):
    server = make_allowlist_server()
    ctx, link, mux = make_ctx(server, identity=IDENTITY_A)
    opened: list[int] = []
    monkeypatch.setattr(
        server, "_open_backend", lambda context, service, stream_id: opened.append(stream_id)
    )
    server._handle_open(ctx, OpenMessage(1, 1, "ssh", "secret"))
    assert wait_for(lambda: opened == [1])
    assert ctx.pending_opens == {}
    assert ctx.identity_timer is None
    assert mux.transport.errors == []
    assert ctx.tracker.failures == 0
    assert server.registry.active_connections("ssh") == 1


def test_open_before_identity_waits_then_authorizes(monkeypatch):
    server = make_allowlist_server(identity_timeout=5.0)
    ctx, link, mux = make_ctx(server)
    server._links[id(link)] = ctx
    opened: list[int] = []
    monkeypatch.setattr(
        server, "_open_backend", lambda context, service, stream_id: opened.append(stream_id)
    )

    server._handle_open(ctx, OpenMessage(1, 1, "ssh", "secret"))

    assert opened == []
    assert list(ctx.pending_opens) == [1]
    assert mux.reserved == {1}
    assert ctx.identity_timer is not None
    timer = ctx.identity_timer
    assert mux.transport.errors == []
    assert ctx.tracker.failures == 0

    server._remote_identified(link, SimpleNamespace(hash=IDENTITY_A))

    assert opened == [1]
    assert ctx.identity_hash == IDENTITY_A
    assert ctx.pending_opens == {}
    assert ctx.identity_timer is None
    assert timer.finished.is_set()
    assert mux.transport.errors == []
    assert link.torn_down is False


def test_identity_arriving_between_check_and_store_wins_the_race(monkeypatch):
    server = make_allowlist_server(identity_timeout=5.0)
    link = RacingLink(SimpleNamespace(hash=IDENTITY_A))
    ctx, link, mux = make_ctx(server, link=link)
    opened: list[int] = []
    monkeypatch.setattr(
        server, "_open_backend", lambda context, service, stream_id: opened.append(stream_id)
    )

    server._handle_open(ctx, OpenMessage(1, 1, "ssh", "secret"))

    assert wait_for(lambda: opened == [1])
    assert ctx.pending_opens == {}
    assert ctx.identity_timer is None
    assert mux.transport.errors == []


def test_identity_timeout_rejects_with_delayed_auth_error(monkeypatch):
    server = make_allowlist_server(identity_timeout=0.05)
    ctx, link, mux = make_ctx(server)
    opened: list[int] = []
    monkeypatch.setattr(
        server, "_open_backend", lambda context, service, stream_id: opened.append(stream_id)
    )

    server._handle_open(ctx, OpenMessage(1, 1, "ssh", "secret"))
    server._handle_open(ctx, OpenMessage(2, 1, "web", ""))

    assert wait_for(lambda: len(mux.transport.errors) >= 2)
    assert sorted(mux.transport.errors) == [
        (1, ErrorCode.AUTH),
        (2, ErrorCode.AUTH),
    ]
    assert ctx.tracker.failures == 2
    assert sorted(mux.released) == [1, 2]
    assert mux.reserved == set()
    assert opened == []
    assert server.registry.active_connections("ssh") == 0
    assert server.registry.active_connections("web") == 0
    assert ctx.identity_timer is None


def test_identity_found_at_timeout_authorizes_pending(monkeypatch):
    server = make_allowlist_server(identity_timeout=0.05)
    link = LateIdentityLink(SimpleNamespace(hash=IDENTITY_A))
    ctx, link, mux = make_ctx(server, link=link)
    opened: list[int] = []
    monkeypatch.setattr(
        server, "_open_backend", lambda context, service, stream_id: opened.append(stream_id)
    )

    server._handle_open(ctx, OpenMessage(1, 1, "ssh", "secret"))
    assert list(ctx.pending_opens) == [1]

    assert wait_for(lambda: opened == [1])
    assert ctx.identity_hash == IDENTITY_A
    assert ctx.pending_opens == {}
    assert ctx.identity_timer is None
    assert mux.transport.errors == []
    assert ctx.tracker.failures == 0


def test_identity_timeout_counts_towards_link_teardown(monkeypatch):
    server = make_allowlist_server(identity_timeout=0.05)
    ctx, link, mux = make_ctx(server)
    monkeypatch.setattr(server, "_open_backend", lambda context, service, stream_id: None)

    for stream_id in range(1, 4):
        server._handle_open(ctx, OpenMessage(stream_id, 1, "ssh", "secret"))

    assert wait_for(lambda: len(mux.transport.errors) >= 3)
    assert sorted(mux.transport.errors) == [
        (1, ErrorCode.AUTH),
        (2, ErrorCode.AUTH),
        (3, ErrorCode.AUTH),
    ]
    assert ctx.tracker.failures == 3
    assert link.torn_down is True


def test_open_before_identity_respects_stream_limit():
    server = make_allowlist_server()
    ctx, link, mux = make_ctx(server, reserve=False)
    server._handle_open(ctx, OpenMessage(1, 1, "ssh", "secret"))
    assert mux.transport.errors == [(1, ErrorCode.LIMIT)]
    assert ctx.pending_opens == {}
    assert ctx.identity_timer is None


def test_open_before_identity_defers_version_check():
    server = make_allowlist_server(identity_timeout=0.05)
    ctx, link, mux = make_ctx(server)
    server._handle_open(ctx, OpenMessage(1, 99, "ssh", "secret"))
    assert list(ctx.pending_opens) == [1]
    assert mux.transport.errors == []
    assert wait_for(lambda: mux.transport.errors == [(1, ErrorCode.AUTH)])
    assert ctx.pending_opens == {}


def test_duplicate_open_while_pending_is_limited():
    server = make_allowlist_server(identity_timeout=5.0)
    ctx, link, mux = make_ctx(server)
    server._handle_open(ctx, OpenMessage(1, 1, "ssh", "secret"))
    server._handle_open(ctx, OpenMessage(1, 1, "ssh", "secret"))
    assert mux.transport.errors == [(1, ErrorCode.LIMIT)]
    assert list(ctx.pending_opens) == [1]
    if ctx.identity_timer is not None:
        ctx.identity_timer.cancel()


def test_public_service_with_allow_list_rejects_unlisted_identity(monkeypatch):
    server = make_allowlist_server()
    ctx, link, mux = make_ctx(server, identity=IDENTITY_B)
    opened: list[int] = []
    monkeypatch.setattr(
        server, "_open_backend", lambda context, service, stream_id: opened.append(stream_id)
    )

    server._handle_open(ctx, OpenMessage(1, 1, "web", ""))

    assert wait_for(lambda: mux.transport.errors == [(1, ErrorCode.AUTH)])
    assert opened == []
    assert ctx.tracker.failures == 1
    assert server.registry.active_connections("web") == 0


def test_public_service_with_allow_list_accepts_allowed_identity(monkeypatch):
    server = make_allowlist_server()
    ctx, link, mux = make_ctx(server, identity=IDENTITY_A)
    opened: list[int] = []
    monkeypatch.setattr(
        server, "_open_backend", lambda context, service, stream_id: opened.append(stream_id)
    )

    server._handle_open(ctx, OpenMessage(1, 1, "web", "stray"))

    assert wait_for(lambda: opened == [1])
    assert ctx.tracker.failures == 0
    assert mux.transport.errors == []
    assert link.torn_down is False


def test_allow_list_still_requires_the_password(monkeypatch):
    server = make_allowlist_server()
    ctx, link, mux = make_ctx(server, identity=IDENTITY_A)
    opened: list[int] = []
    monkeypatch.setattr(
        server, "_open_backend", lambda context, service, stream_id: opened.append(stream_id)
    )

    server._handle_open(ctx, OpenMessage(1, 1, "ssh", "wrong"))

    assert wait_for(lambda: mux.transport.errors == [(1, ErrorCode.AUTH)])
    assert opened == []


def test_probe_allowed_and_denied_are_indistinguishable(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    server = make_allowlist_server()
    allowed = server._handle_probe(
        *args(
            {"service": "ssh", "password": "secret"},
            SimpleNamespace(hash=IDENTITY_A),
        )
    )
    assert allowed == {
        "ok": True,
        "service": {"name": "ssh", "protocol": "tcp", "auth": True},
    }
    denied = server._handle_probe(
        *args(
            {"service": "ssh", "password": "secret"},
            SimpleNamespace(hash=IDENTITY_B),
        )
    )
    anonymous = server._handle_probe(
        *args({"service": "ssh", "password": "secret"})
    )
    missing = server._handle_probe(
        *args({"service": "nope", "password": "secret"}, SimpleNamespace(hash=IDENTITY_A))
    )
    assert denied == anonymous == missing == {"ok": False, "error": "auth"}


def test_probe_public_service_with_allow_list(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    server = make_allowlist_server()
    assert server._handle_probe(
        *args({"service": "web", "password": ""}, SimpleNamespace(hash=IDENTITY_A))
    ) == {"ok": True, "service": {"name": "web", "protocol": "tcp", "auth": True}}
    assert server._handle_probe(
        *args({"service": "web", "password": ""}, SimpleNamespace(hash=IDENTITY_B))
    ) == {"ok": False, "error": "auth"}


def test_udp_service_with_allow_list_rejects_unlisted_identity(monkeypatch):
    server = PortfwdServer(
        services=[
            Service(
                "127.0.0.1",
                53,
                "dns",
                "secret",
                protocol="udp",
                allowed_identities=frozenset({IDENTITY_A}),
            )
        ],
        list_password="listpass",
        identity_path="/tmp/unused.id",
        name="test",
        config_dir=None,
        logger=LOGGER,
        max_connections_per_service=2,
        identity_timeout=0.05,
    )
    ctx, link, mux = make_ctx(server)
    opened: list[int] = []
    monkeypatch.setattr(
        server, "_open_backend", lambda context, service, stream_id: opened.append(stream_id)
    )

    server._handle_open(ctx, OpenMessage(1, 1, "dns", "secret"))

    assert wait_for(lambda: mux.transport.errors == [(1, ErrorCode.AUTH)])
    assert opened == []
    assert ctx.tracker.failures == 1
    assert server.registry.active_connections("dns") == 0


def test_list_is_not_filtered_by_identity(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    server = make_allowlist_server()
    result = server._handle_list(
        *args({"password": "listpass"}, SimpleNamespace(hash=IDENTITY_B))
    )
    assert [item["name"] for item in result["services"]] == ["ssh", "web"]
    assert {item["name"]: item["auth"] for item in result["services"]} == {
        "ssh": True,
        "web": True,
    }
    assert IDENTITY_A.hex() not in str(result)
    assert IDENTITY_B.hex() not in str(result)


def test_link_closed_clears_pending_opens_without_releasing_service_slots():
    server = make_allowlist_server(identity_timeout=5.0)
    ctx, link, mux = make_ctx(server)
    server._links[id(link)] = ctx
    server._handle_open(ctx, OpenMessage(1, 1, "ssh", "secret"))
    timer = ctx.identity_timer
    assert timer is not None

    server._link_closed(link)

    assert ctx.pending_opens == {}
    assert ctx.identity_timer is None
    assert timer.finished.is_set()
    assert ctx.closing is True
    assert mux.closed is True
    assert server.registry.active_connections("ssh") == 0
