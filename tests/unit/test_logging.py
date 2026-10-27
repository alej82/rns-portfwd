from __future__ import annotations

import logging

from rns_portfwd.logging import RedactionFilter, redact, register_secret


def test_redact_replaces_registered_secrets():
    register_secret("hunter2")
    assert redact("password hunter2 here") == "password *** here"
    assert redact("nothing") == "nothing"
    assert redact("") == ""


def test_filter_redacts_formatted_record():
    register_secret("topsecret")
    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1, "value is topsecret", (), None
    )
    RedactionFilter().filter(record)
    assert "topsecret" not in record.getMessage()
    assert record.getMessage() == "value is ***"
