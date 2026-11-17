from __future__ import annotations

import builtins
import math
import threading
from types import SimpleNamespace

import pytest
import RNS

from rns_portfwd import discovery
from rns_portfwd.config import validate_link_stamp_cost
from rns_portfwd.constants import (
    DEFAULT_HALF_OPEN_TIMEOUT,
    DEFAULT_MAX_HALF_OPEN,
)
from rns_portfwd.discovery import (
    LinkRequestGuard,
    StampedDestination,
    announce_data,
    configure_link_stamping,
    link_stamp_generation_supported,
    link_stamp_validation_supported,
    make_in_destination,
    parse_announce_data,
    parse_stamp_cost,
    set_default_app_data,
)
from rns_portfwd.errors import ConfigError, ResolutionError

DEST_HASH = bytes.fromhex("0123456789abcdef0123456789abcdef")
KEY_HALF = RNS.Link.ECPUBSIZE // 2
PUB = b"\x11" * KEY_HALF
SIG_PUB = b"\x22" * KEY_HALF


def request(pub: bytes = PUB, sig_pub: bytes = SIG_PUB, mtu: bool = False) -> bytes:
    data = pub + sig_pub
    return data + b"\x00" * RNS.Link.LINK_MTU_SIZE if mtu else data


def links_with(count: int, status=RNS.Link.PENDING) -> list:
    return [SimpleNamespace(status=status) for _ in range(count)]


def make_guard(**kwargs) -> LinkRequestGuard:
    return LinkRequestGuard(DEST_HASH, **kwargs)


# --- payload sanity -------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        b"",
        bytes(KEY_HALF),
        bytes(RNS.Link.ECPUBSIZE - 1),
        bytes(RNS.Link.ECPUBSIZE + 1),
        bytes(RNS.Link.ECPUBSIZE + RNS.Link.LINK_MTU_SIZE + 1),
        None,
        "x" * RNS.Link.ECPUBSIZE,
    ],
)
def test_guard_rejects_malformed_payloads(data):
    allowed, pair, reason = make_guard().check(data, [])
    assert allowed is False
    assert pair is None
    assert reason == "malformed link request"


def test_guard_accepts_ecpub_and_mtu_payloads():
    guard = make_guard()
    assert guard.check(request(), [])[:2] == (True, None)
    assert guard.check(request(mtu=True), [])[:2] == (True, None)


# --- cost 0 ---------------------------------------------------------------


def test_cost_zero_does_not_import_stamps(monkeypatch):
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "RNS" and "Stamps" in (fromlist or ()):
            raise AssertionError("RNS.Stamps must not be imported when cost is 0")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    guard = make_guard(stamp_cost=0.0)
    for _ in range(5):
        assert guard.check(request(), [])[0] is True


def test_cost_zero_still_enforces_half_open_cap():
    guard = make_guard(stamp_cost=0.0, max_half_open=2)
    assert guard.check(request(), links_with(1))[0] is True
    assert guard.check(request(), links_with(2))[0] is False


# --- half-open cap --------------------------------------------------------


@pytest.mark.parametrize(
    ("max_half_open", "open_links", "expected"),
    [
        (3, 0, True),
        (3, 2, True),
        (3, 3, False),
        (3, 4, False),
        (1, 0, True),
        (1, 1, False),
        (0, 1000, True),
        (DEFAULT_MAX_HALF_OPEN, DEFAULT_MAX_HALF_OPEN - 1, True),
        (DEFAULT_MAX_HALF_OPEN, DEFAULT_MAX_HALF_OPEN, False),
        (DEFAULT_MAX_HALF_OPEN, DEFAULT_MAX_HALF_OPEN + 1, False),
    ],
)
def test_half_open_cap(max_half_open, open_links, expected):
    guard = make_guard(max_half_open=max_half_open)
    allowed, pair, reason = guard.check(request(), links_with(open_links))
    assert allowed is expected
    assert pair is None
    assert reason == ("" if expected else "half-open link limit")


def test_half_open_counts_only_pending_and_handshake():
    guard = make_guard(max_half_open=2)
    mixed = [
        SimpleNamespace(status=RNS.Link.PENDING),
        SimpleNamespace(status=RNS.Link.HANDSHAKE),
        SimpleNamespace(status=RNS.Link.ACTIVE),
        SimpleNamespace(status=RNS.Link.CLOSED),
    ]
    assert guard.check(request(), mixed)[0] is False
    assert guard.check(request(), mixed[:1] + mixed[2:])[0] is True


def test_half_open_missing_status_is_not_counted():
    guard = make_guard(max_half_open=1)
    assert guard.check(request(), [SimpleNamespace()])[0] is True
    assert guard.check(request(), [None])[0] is True


def test_half_open_tolerates_uniterable_links():
    guard = make_guard(max_half_open=1)

    class BadLinks:
        def __iter__(self):
            raise RuntimeError("boom")

    assert guard.check(request(), BadLinks())[0] is True


# --- stamp validation -----------------------------------------------------


def test_valid_stamp_material_is_destination_hash_plus_sig_pub(monkeypatch):
    calls = []

    def fake_stamp_valid(stamp, target_cost, material):
        calls.append((stamp, target_cost, material))
        return True

    monkeypatch.setattr(RNS.Stamps, "stamp_valid", fake_stamp_valid)
    guard = make_guard(stamp_cost=8.0)
    allowed, pair, reason = guard.check(request(), [])
    assert allowed is True
    assert reason == ""
    assert pair == (PUB, SIG_PUB)
    assert calls == [(PUB, 8.0, DEST_HASH + SIG_PUB)]


def test_invalid_stamp_is_dropped(monkeypatch):
    monkeypatch.setattr(RNS.Stamps, "stamp_valid", lambda *a: False)
    guard = make_guard(stamp_cost=8.0)
    allowed, pair, reason = guard.check(request(), [])
    assert allowed is False
    assert pair is None
    assert reason == "insufficient link stamp"


def test_stamp_validation_error_is_dropped(monkeypatch):
    def explode(*args):
        raise ValueError("bad keys")

    monkeypatch.setattr(RNS.Stamps, "stamp_valid", explode)
    allowed, pair, reason = make_guard(stamp_cost=8.0).check(request(), [])
    assert allowed is False
    assert pair is None
    assert reason == "stamp validation failed"


def test_stamp_checked_only_after_half_open(monkeypatch):
    calls = []
    monkeypatch.setattr(
        RNS.Stamps, "stamp_valid", lambda *a: calls.append(a) or True
    )
    guard = make_guard(stamp_cost=8.0, max_half_open=1)
    assert guard.check(request(), links_with(1))[0] is False
    assert calls == []


def test_rejected_request_logs_nothing_warning_or_higher(caplog):
    guard = make_guard(max_half_open=0)
    with caplog.at_level("DEBUG"):
        assert guard.check(b"short", [])[0] is False
    assert [record for record in caplog.records if record.levelno >= 30] == []


# --- replay cache ---------------------------------------------------------


def test_immediate_replay_is_rejected_until_released(monkeypatch):
    monkeypatch.setattr(RNS.Stamps, "stamp_valid", lambda *a: True)
    guard = make_guard(stamp_cost=8.0)
    allowed, pair, _ = guard.check(request(), [])
    assert allowed is True
    assert pair is not None
    assert guard.check(request(), [])[:2] == (False, None)
    assert guard.check(request(), [])[2] == "replayed link stamp"

    guard.release(pair)
    assert guard.check(request(), [])[0] is True


def test_replay_cache_expires_after_ttl(monkeypatch):
    monkeypatch.setattr(RNS.Stamps, "stamp_valid", lambda *a: True)
    now = [1000.0]
    monkeypatch.setattr(discovery.time, "monotonic", lambda: now[0])
    guard = make_guard(stamp_cost=8.0, cache_ttl=10.0)

    assert guard.check(request(), [])[0] is True
    now[0] += 10.0
    assert guard.check(request(), [])[0] is False
    now[0] += 0.1
    assert guard.check(request(), [])[0] is True


def test_replay_cache_evicts_oldest_at_capacity(monkeypatch):
    monkeypatch.setattr(RNS.Stamps, "stamp_valid", lambda *a: True)
    guard = make_guard(stamp_cost=8.0, cache_max=2)
    first = request(pub=b"\x01" * KEY_HALF)
    second = request(pub=b"\x02" * KEY_HALF)
    third = request(pub=b"\x03" * KEY_HALF)

    assert guard.check(first, [])[0] is True
    assert guard.check(second, [])[0] is True
    assert guard.check(third, [])[0] is True
    assert guard.check(second, [])[0] is False
    assert guard.check(first, [])[0] is True
    assert guard.check(second, [])[0] is True


def test_replay_cache_never_exceeds_capacity(monkeypatch):
    monkeypatch.setattr(RNS.Stamps, "stamp_valid", lambda *a: True)
    guard = make_guard(stamp_cost=8.0, cache_max=3)
    for index in range(20):
        assert guard.check(request(pub=bytes([index]) * KEY_HALF), [])[0] is True
    assert len(guard._cache) == 3


def test_release_of_unknown_pair_is_harmless():
    guard = make_guard(stamp_cost=8.0)
    guard.release((b"x" * KEY_HALF, b"y" * KEY_HALF))


# --- announce data --------------------------------------------------------


def test_announce_includes_positive_stamp_cost():
    parsed = parse_announce_data(announce_data("my server", True, 12.5))
    assert parsed == {"v": 1, "name": "my server", "auth": True, "stamp_cost": 12.5}


@pytest.mark.parametrize("cost", [None, 0, 0.0, -1.0])
def test_announce_omits_non_positive_stamp_cost(cost):
    parsed = parse_announce_data(announce_data("my server", False, cost))
    assert parsed == {"v": 1, "name": "my server", "auth": False}


def test_announce_without_cost_arg_is_unchanged():
    parsed = parse_announce_data(announce_data("my server", True))
    assert parsed == {"v": 1, "name": "my server", "auth": True}


def test_set_default_app_data_forwards_stamp_cost():
    class FakeDestination:
        def __init__(self):
            self.raw = None

        def set_default_app_data(self, raw):
            self.raw = raw

    destination = FakeDestination()
    set_default_app_data(destination, "srv", False, 8)
    assert parse_announce_data(destination.raw)["stamp_cost"] == 8
    set_default_app_data(destination, "srv", False, 0)
    assert "stamp_cost" not in parse_announce_data(destination.raw)


@pytest.mark.parametrize(
    ("app_data", "expected"),
    [
        ({"stamp_cost": 0}, 0.0),
        ({"stamp_cost": 8}, 8.0),
        ({"stamp_cost": 12.5}, 12.5),
        ({"stamp_cost": 40}, 40.0),
        ({"stamp_cost": 40.1}, 0.0),
        ({"stamp_cost": -1}, 0.0),
        ({"stamp_cost": "8"}, 0.0),
        ({"stamp_cost": True}, 0.0),
        ({"stamp_cost": False}, 0.0),
        ({"stamp_cost": math.nan}, 0.0),
        ({"stamp_cost": math.inf}, 0.0),
        ({"name": "srv"}, 0.0),
        ({}, 0.0),
        (None, 0.0),
        (b"raw", 0.0),
    ],
)
def test_parse_stamp_cost(app_data, expected):
    assert parse_stamp_cost(app_data) == expected


# --- cost validation ------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, 0.0), (12.5, 12.5), (40, 40.0), (0.0, 0.0)],
)
def test_validate_link_stamp_cost_accepts_range(value, expected):
    assert validate_link_stamp_cost(value) == expected


@pytest.mark.parametrize(
    "value",
    [-1, -0.1, 40.1, 100, math.nan, math.inf, -math.inf, "abc", None, True, False],
)
def test_validate_link_stamp_cost_rejects_invalid(value):
    with pytest.raises(ConfigError):
        validate_link_stamp_cost(value)


# --- capability probes ----------------------------------------------------


def test_validation_supported_with_capabilities(monkeypatch):
    monkeypatch.setattr(discovery.importlib.util, "find_spec", lambda name: object())
    assert link_stamp_validation_supported() is True


def test_validation_unsupported_without_stamps_module(monkeypatch):
    monkeypatch.setattr(discovery.importlib.util, "find_spec", lambda name: None)
    assert link_stamp_validation_supported() is False


def test_validation_unsupported_without_set_link_cost(monkeypatch):
    monkeypatch.setattr(discovery.importlib.util, "find_spec", lambda name: object())
    monkeypatch.delattr(RNS.Destination, "set_link_cost")
    assert link_stamp_validation_supported() is False


def test_validation_probe_handles_import_error(monkeypatch):
    def boom(name):
        raise ImportError(name)

    monkeypatch.setattr(discovery.importlib.util, "find_spec", boom)
    assert link_stamp_validation_supported() is False


def test_generation_supported_with_capabilities(monkeypatch):
    monkeypatch.setattr(discovery.importlib.util, "find_spec", lambda name: object())
    assert link_stamp_generation_supported() is True


def test_generation_unsupported_without_stamps_module(monkeypatch):
    monkeypatch.setattr(discovery.importlib.util, "find_spec", lambda name: None)
    assert link_stamp_generation_supported() is False


def test_generation_unsupported_without_transport_flag(monkeypatch):
    monkeypatch.setattr(discovery.importlib.util, "find_spec", lambda name: object())
    monkeypatch.delattr(RNS.Transport, "ALWAYS_STAMP_LRS")
    assert link_stamp_generation_supported() is False


def test_default_half_open_timeout_constant_is_positive():
    assert DEFAULT_HALF_OPEN_TIMEOUT > 0


def test_configure_link_stamping_parses_raw_announce_bytes(monkeypatch):
    raw = announce_data("server", True, 8)
    monkeypatch.setattr(
        RNS.Identity, "recall_app_data", staticmethod(lambda destination_hash: raw)
    )
    monkeypatch.setattr(discovery, "link_stamp_generation_supported", lambda: True)
    monkeypatch.setattr(RNS.Transport, "ALWAYS_STAMP_LRS", False)
    monkeypatch.setattr(RNS.Transport, "LR_COST", 12.5)

    cost = configure_link_stamping(DEST_HASH)

    assert cost == 8.0
    assert RNS.Transport.ALWAYS_STAMP_LRS is True
    assert RNS.Transport.LR_COST == 8.0


def test_configure_link_stamping_disabled_turns_off_generation(monkeypatch):
    monkeypatch.setattr(RNS.Transport, "ALWAYS_STAMP_LRS", True)
    monkeypatch.setattr(RNS.Transport, "STAMP_LRS", True)

    assert configure_link_stamping(DEST_HASH, disabled=True) == 0.0
    assert RNS.Transport.ALWAYS_STAMP_LRS is False
    assert RNS.Transport.STAMP_LRS is False


def test_make_in_destination_rejects_unsupported_stamping(monkeypatch):
    monkeypatch.setattr(discovery, "link_stamp_validation_supported", lambda: False)
    with pytest.raises(ConfigError):
        make_in_destination(object(), stamp_cost=8)


class FakeGuard:
    def __init__(self, allowed=True, pair=("pub", "sig")):
        self.allowed = allowed
        self.pair = pair
        self.released = []

    def check(self, data, links):
        return self.allowed, self.pair if self.allowed else None, ""

    def release(self, pair):
        self.released.append(pair)


class FakeDestinationLink:
    def __init__(self, timeout=390.0):
        self.establishment_timeout = timeout


def fake_destination(guard):
    destination = object.__new__(StampedDestination)
    destination.accept_link_requests = True
    destination.links = []
    destination.half_open_timeout = 30.0
    destination.link_guard = guard
    return destination


def test_destination_drops_before_calling_super(monkeypatch):
    called = []
    monkeypatch.setattr(
        RNS.Destination,
        "incoming_link_request",
        lambda self, data, packet: called.append(True),
    )
    destination = fake_destination(FakeGuard(allowed=False))

    destination.incoming_link_request(b"request", None)

    assert called == []


def test_destination_clamps_new_link_timeout(monkeypatch):
    guard = FakeGuard()
    destination = fake_destination(guard)

    def append_link(self, data, packet):
        self.links.append(FakeDestinationLink())

    monkeypatch.setattr(RNS.Destination, "incoming_link_request", append_link)

    destination.incoming_link_request(b"request", None)

    assert destination.links[0].establishment_timeout == 30.0
    assert guard.released == []


def test_destination_releases_reservation_without_new_link(monkeypatch):
    guard = FakeGuard(pair=("pub", "sig"))
    destination = fake_destination(guard)
    monkeypatch.setattr(
        RNS.Destination, "incoming_link_request", lambda self, data, packet: None
    )

    destination.incoming_link_request(b"request", None)

    assert guard.released == [("pub", "sig")]


def test_destination_releases_reservation_when_super_raises(monkeypatch):
    guard = FakeGuard(pair=("pub", "sig"))
    destination = fake_destination(guard)

    def boom(self, data, packet):
        raise RuntimeError("registration failed")

    monkeypatch.setattr(RNS.Destination, "incoming_link_request", boom)

    with pytest.raises(RuntimeError):
        destination.incoming_link_request(b"request", None)
    assert guard.released == [("pub", "sig")]


def test_destination_without_guard_drops_without_calling_super(monkeypatch):
    called = []
    monkeypatch.setattr(
        RNS.Destination,
        "incoming_link_request",
        lambda self, data, packet: called.append(True),
    )
    destination = fake_destination(None)

    destination.incoming_link_request(b"request", None)

    assert called == []


def test_make_in_destination_pins_native_cost_even_at_zero(monkeypatch):
    class FakeDestination:
        def __init__(self, *args, **kwargs):
            self.hash = DEST_HASH
            self.link_cost = None

        def set_link_cost(self, cost):
            self.link_cost = cost

    monkeypatch.setattr(discovery, "StampedDestination", FakeDestination)
    destination = make_in_destination(object(), stamp_cost=0)
    assert destination.link_cost == 0.0


def test_configure_link_stamping_fails_fast_without_generation(monkeypatch):
    raw = announce_data("server", True, 8)
    monkeypatch.setattr(
        RNS.Identity, "recall_app_data", staticmethod(lambda destination_hash: raw)
    )
    monkeypatch.setattr(discovery, "link_stamp_generation_supported", lambda: False)

    with pytest.raises(ResolutionError):
        configure_link_stamping(DEST_HASH)


def test_configure_link_stamping_cost_zero_keeps_native_flag(monkeypatch):
    raw = announce_data("server", True, 0)
    monkeypatch.setattr(
        RNS.Identity, "recall_app_data", staticmethod(lambda destination_hash: raw)
    )
    monkeypatch.setattr(RNS.Transport, "ALWAYS_STAMP_LRS", True)
    monkeypatch.setattr(RNS.Transport, "STAMP_LRS", True)

    assert configure_link_stamping(DEST_HASH) == 0.0
    assert RNS.Transport.ALWAYS_STAMP_LRS is False
    assert RNS.Transport.STAMP_LRS is True


def test_configure_link_stamping_override_zero_disables_native_stamping(monkeypatch):
    monkeypatch.setattr(RNS.Transport, "ALWAYS_STAMP_LRS", True)
    monkeypatch.setattr(RNS.Transport, "STAMP_LRS", True)

    assert configure_link_stamping(DEST_HASH, override=0) == 0.0
    assert RNS.Transport.ALWAYS_STAMP_LRS is False
    assert RNS.Transport.STAMP_LRS is False


def test_configure_link_stamping_without_app_data_is_anonymous(monkeypatch):
    monkeypatch.setattr(
        RNS.Identity, "recall_app_data", staticmethod(lambda destination_hash: None)
    )
    monkeypatch.setattr(RNS.Transport, "ALWAYS_STAMP_LRS", True)

    assert configure_link_stamping(DEST_HASH) == 0.0
    assert RNS.Transport.ALWAYS_STAMP_LRS is False


def test_guard_cost_zero_never_calls_stamp_valid(monkeypatch):
    from RNS import Stamps

    calls = []
    monkeypatch.setattr(
        Stamps, "stamp_valid", lambda *args: calls.append(args) or True
    )
    guard = LinkRequestGuard(DEST_HASH, stamp_cost=0)

    allowed, pair, _ = guard.check(request(), [])

    assert allowed is True
    assert pair is None
    assert calls == []


def test_guard_replay_cache_is_thread_safe(monkeypatch):
    from RNS import Stamps

    monkeypatch.setattr(Stamps, "stamp_valid", lambda *args: True)
    guard = LinkRequestGuard(DEST_HASH, stamp_cost=8)
    results = []
    barrier = threading.Barrier(8)

    def attempt():
        barrier.wait()
        allowed, _, _ = guard.check(request(), [])
        results.append(allowed)

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count(True) == 1
    assert results.count(False) == 7
