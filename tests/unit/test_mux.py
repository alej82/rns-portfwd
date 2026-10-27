from __future__ import annotations

import logging
import threading
import time

from rns_portfwd.mux import Mux

LOGGER = logging.getLogger("test.mux")


class FakeChannel:
    def __init__(self, mdu=431):
        self.mdu = mdu
        self.handlers = []
        self.sent = []

    def add_message_handler(self, handler):
        self.handlers.append(handler)

    def send(self, message):
        self.sent.append(message)


class FakeStream:
    def __init__(self):
        self.events = []
        self.aborted = None

    def on_remote_data(self, data, eof, compressed):
        self.events.append(("data", data, eof, compressed))

    def on_window(self, credit):
        self.events.append(("window", credit))

    def on_remote_close(self, reason):
        self.events.append(("close", reason))

    def on_open_ok(self, credit):
        self.events.append(("ok", credit))

    def on_open_err(self, code):
        self.events.append(("err", code))

    def abort(self, reason, send_close=False):
        self.aborted = reason


def make_mux(is_client=True, max_streams=2, open_handler=None):
    channel = FakeChannel()
    mux = Mux(
        channel,
        window=1024,
        compress=False,
        max_streams=max_streams,
        logger=LOGGER,
        is_client=is_client,
        open_handler=open_handler,
    )
    return mux, channel


def test_allocate_is_monotonic_and_never_reuses():
    mux, _ = make_mux()
    assert mux.allocate_id() == 1
    assert mux.allocate_id() == 2
    mux._next_id = 0x3FFF
    assert mux.allocate_id() == 0x3FFF
    assert mux.allocate_id() is None


def test_reserve_install_and_limit():
    mux, _ = make_mux(max_streams=1)
    assert mux.reserve(1) is True
    assert mux.reserve(1) is False
    assert mux.reserve(2) is False
    stream = FakeStream()
    assert mux.install(1, stream) is True  # type: ignore[arg-type]
    assert mux.get(1) is stream
    assert mux.count == 1
    mux.remove(1)
    assert mux.get(1) is None
    assert mux.count == 0


def test_handle_dispatches_messages():
    from rns_portfwd.protocol import (
        CloseMessage,
        DataMessage,
        OpenErrMessage,
        OpenOkMessage,
        WindowMessage,
    )

    mux, channel = make_mux()
    stream = FakeStream()
    assert mux.reserve(1)
    assert mux.install(1, stream)  # type: ignore[arg-type]
    mux.handle(DataMessage(1, b"hi", eof=True, compressed=True))
    mux.handle(WindowMessage(1, 50))
    mux.handle(OpenOkMessage(1, 100))
    mux.handle(OpenErrMessage(1, 1))
    mux.handle(CloseMessage(1, 2))
    assert stream.events == [
        ("data", b"hi", True, True),
        ("window", 50),
        ("ok", 100),
        ("err", 1),
        ("close", 2),
    ]
    assert channel.handlers == [mux.handle]


def test_open_message_goes_to_handler():
    from rns_portfwd.protocol import OpenMessage

    seen = []
    mux, _ = make_mux(open_handler=seen.append)
    message = OpenMessage(1, 1, "ssh", "pw")
    assert mux.handle(message) is True
    assert seen == [message]


def test_wait_for_slot_wakes_on_remove():
    mux, _ = make_mux(max_streams=1)
    mux.reserve(1)
    mux.install(1, FakeStream())  # type: ignore[arg-type]
    stop = threading.Event()
    result = []

    def waiter():
        result.append(mux.wait_for_slot(2.0, stop))

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.05)
    assert result == []
    mux.remove(1)
    thread.join(1)
    assert result == [True]


def test_close_all_aborts_streams():
    mux, _ = make_mux()
    first = FakeStream()
    second = FakeStream()
    mux.reserve(1)
    mux.reserve(2)
    mux.install(1, first)  # type: ignore[arg-type]
    mux.install(2, second)  # type: ignore[arg-type]
    mux.close_all("gone", send_close=False)
    assert first.aborted == "gone"
    assert second.aborted == "gone"
    assert mux.count == 0
    assert mux.closed is True
