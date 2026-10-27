from __future__ import annotations

import threading

import pytest
import RNS

from rns_portfwd.errors import ResolutionError
from rns_portfwd.link_manager import LinkManager


class FakeLink:
    def __init__(self, destination, established_callback=None, closed_callback=None):
        self.destination = destination
        self.established_callback = established_callback
        self.closed_callback = closed_callback
        self.status = RNS.Link.PENDING
        self.torn_down = False

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
