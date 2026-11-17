from __future__ import annotations

import logging
import threading

import pytest
import RNS

from rns_portfwd.errors import ResolutionError
from rns_portfwd.link_manager import LinkManager

LOGGER = logging.getLogger("test.link_manager")


class FakeLink:
    def __init__(self, destination, established_callback=None, closed_callback=None):
        self.destination = destination
        self.established_callback = established_callback
        self.closed_callback = closed_callback
        self.status = RNS.Link.PENDING
        self.torn_down = False
        self.identified = []

    def identify(self, identity):
        self.identified.append(identity)

    def establish(self):
        self.status = RNS.Link.ACTIVE
        if self.established_callback:
            self.established_callback(self)

    def teardown(self):
        self.status = RNS.Link.CLOSED
        self.torn_down = True


class SlowLink(FakeLink):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        threading.Timer(0.05, self.establish).start()


def test_open_waits_for_established(monkeypatch):
    monkeypatch.setattr(
        "rns_portfwd.link_manager.make_out_destination", lambda identity: object()
    )
    manager = LinkManager(object(), timeout=1, link_factory=SlowLink)
    link = manager.open()
    assert link.status == RNS.Link.ACTIVE
    assert manager.established is True
    manager.close()
    assert link.status == RNS.Link.CLOSED


def test_open_times_out(monkeypatch):
    monkeypatch.setattr(
        "rns_portfwd.link_manager.make_out_destination", lambda identity: object()
    )
    manager = LinkManager(object(), timeout=0.1, link_factory=FakeLink)
    with pytest.raises(ResolutionError):
        manager.open()


def test_close_callback_is_forwarded(monkeypatch):
    monkeypatch.setattr(
        "rns_portfwd.link_manager.make_out_destination", lambda identity: object()
    )
    seen = []
    manager = LinkManager(
        object(),
        timeout=1,
        link_factory=SlowLink,
        on_closed=lambda link: seen.append(link),
    )
    link = manager.open()
    link.closed_callback(link)
    assert manager.closed is True
    assert seen == [link]


def _manager(link_factory, **kwargs):
    return LinkManager(
        object(),
        timeout=1,
        link_factory=link_factory,
        logger=LOGGER,
        **kwargs,
    )


def test_identify_called_on_establish(monkeypatch):
    monkeypatch.setattr(
        "rns_portfwd.link_manager.make_out_destination", lambda identity: object()
    )
    client_identity = object()
    manager = _manager(SlowLink, client_identity=client_identity)
    link = manager.open()
    assert manager.established is True
    assert link.identified == [client_identity]
    manager.close()


def test_identify_runs_once_per_establishment(monkeypatch):
    monkeypatch.setattr(
        "rns_portfwd.link_manager.make_out_destination", lambda identity: object()
    )
    manager = _manager(FakeLink, client_identity=object())
    link = FakeLink(manager.destination)
    manager._on_established(link)
    manager._on_established(link)
    assert len(link.identified) == 1


def test_identify_skipped_without_client_identity(monkeypatch):
    monkeypatch.setattr(
        "rns_portfwd.link_manager.make_out_destination", lambda identity: object()
    )
    manager = _manager(SlowLink)
    link = manager.open()
    assert link.identified == []
    manager.close()


def test_identify_failure_does_not_abort_establishment(monkeypatch, caplog):
    monkeypatch.setattr(
        "rns_portfwd.link_manager.make_out_destination", lambda identity: object()
    )

    class BrokenIdentifyLink(SlowLink):
        def identify(self, identity):
            raise RuntimeError("boom")

    manager = _manager(BrokenIdentifyLink, client_identity=object())
    with caplog.at_level(logging.WARNING, logger=LOGGER.name):
        link = manager.open()
    assert link.status == RNS.Link.ACTIVE
    assert manager.established is True
    assert "could not identify the Link" in caplog.text
    manager.close()
