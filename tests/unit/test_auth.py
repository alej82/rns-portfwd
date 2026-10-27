from __future__ import annotations

from rns_portfwd.auth import AuthTracker, constant_time_equals, dummy_compare


def test_constant_time_equals():
    assert constant_time_equals("secret", "secret")
    assert constant_time_equals(b"secret", b"secret")
    assert not constant_time_equals("secret", "secreu")
    assert not constant_time_equals("secret", None)
    assert constant_time_equals(None, None)
    assert not constant_time_equals("", None)


def test_dummy_compare_does_not_raise():
    dummy_compare("anything")
    dummy_compare(None)


def test_auth_tracker_threshold():
    tracker = AuthTracker(max_failures=3)
    assert tracker.failures == 0
    assert tracker.record_failure() is False
    assert tracker.record_failure() is False
    assert tracker.record_failure() is True
    assert tracker.failures == 3
    tracker.reset()
    assert tracker.failures == 0
