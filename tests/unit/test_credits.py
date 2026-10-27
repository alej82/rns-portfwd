from __future__ import annotations

import threading
import time

import pytest

from rns_portfwd.credits import CreditWindow


def test_add_take_and_properties():
    window = CreditWindow(initial=10, max_credit=20)
    assert window.available == 10
    assert window.take(4, threading.Event()) is True
    assert window.available == 6
    assert window.add(5) is True
    assert window.available == 11
    assert window.add(100) is True
    assert window.available == 20


def test_take_waits_for_credit():
    window = CreditWindow(initial=0)
    closed = threading.Event()
    result = []

    def take():
        result.append(window.take(8, closed, timeout=2))

    thread = threading.Thread(target=take)
    thread.start()
    time.sleep(0.05)
    assert result == []
    window.add(8)
    thread.join(1)
    assert result == [True]


def test_take_returns_false_when_closed():
    window = CreditWindow(initial=0)
    closed = threading.Event()
    thread_result = []
    thread = threading.Thread(
        target=lambda: thread_result.append(window.take(1, closed, timeout=2))
    )
    thread.start()
    time.sleep(0.05)
    window.close()
    thread.join(1)
    assert thread_result == [False]
    assert window.take(1, threading.Event()) is False


def test_take_wakes_on_event():
    window = CreditWindow(initial=0)
    closed = threading.Event()
    thread_result = []
    thread = threading.Thread(
        target=lambda: thread_result.append(window.take(1, closed, timeout=2))
    )
    thread.start()
    time.sleep(0.05)
    closed.set()
    window.wake()
    thread.join(1)
    assert thread_result == [False]


def test_invalid_values():
    with pytest.raises(ValueError):
        CreditWindow(initial=-1)
    with pytest.raises(ValueError):
        CreditWindow(initial=2, max_credit=1)
    window = CreditWindow()
    with pytest.raises(ValueError):
        window.add(0)
    with pytest.raises(ValueError):
        window.add(-1)
    assert window.take(0, threading.Event()) is True
