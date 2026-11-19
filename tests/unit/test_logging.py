from __future__ import annotations

import logging

import pytest

from rns_portfwd.logging import (
    RedactionFilter,
    redact,
    register_secret,
    rns_loglevel,
    setup_logging,
)


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


@pytest.mark.parametrize(
    ("verbosity", "expected"),
    [
        (0, logging.INFO),
        (1, logging.DEBUG),
        (2, logging.DEBUG),
        (3, logging.DEBUG),
    ],
)
def test_setup_logging_levels(verbosity, expected):
    logger = logging.getLogger("rns_portfwd")
    handlers = logger.handlers[:]
    level = logger.level
    propagate = logger.propagate
    logger.handlers.clear()
    try:
        assert setup_logging(verbosity) is logger
        assert logger.level == expected
        assert logger.handlers[0].level == expected
        assert logger.propagate is False
    finally:
        logger.handlers.clear()
        logger.handlers.extend(handlers)
        logger.setLevel(level)
        logger.propagate = propagate


def test_rns_loglevel_mapping():
    import RNS

    assert rns_loglevel(0) == RNS.LOG_CRITICAL
    assert rns_loglevel(1) == RNS.LOG_NOTICE
    assert rns_loglevel(2) == RNS.LOG_DEBUG
    assert rns_loglevel(3) == RNS.LOG_DEBUG
    assert rns_loglevel(2) > rns_loglevel(1)
