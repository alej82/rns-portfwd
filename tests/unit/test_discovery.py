from __future__ import annotations

import os
import stat

import pytest
import RNS

from rns_portfwd.discovery import (
    announce_data,
    load_or_create_identity,
    parse_announce_data,
    parse_destination_hash,
    resolve_identity,
)
from rns_portfwd.errors import IdentityError, ResolutionError


def test_parse_destination_hash():
    text = "8ed9308efb95ff5bc8a5253ab9789116"
    assert parse_destination_hash(text) == bytes.fromhex(text)
    assert parse_destination_hash(text.upper()) == bytes.fromhex(text)
    for invalid in ("abc", "z" * 32, "0" * 33):
        with pytest.raises(ResolutionError):
            parse_destination_hash(invalid)


def test_identity_create_and_reload(tmp_path):
    path = tmp_path / "server.id"
    identity = load_or_create_identity(str(path))
    assert path.exists()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    again = load_or_create_identity(str(path))
    assert again.hash == identity.hash
    assert again.get_private_key() == identity.get_private_key()


def test_corrupt_identity_is_not_replaced(tmp_path):
    path = tmp_path / "server.id"
    path.write_text("not an identity")
    with pytest.raises(IdentityError):
        load_or_create_identity(str(path))
    assert path.read_text() == "not an identity"


def test_announce_payload():
    raw = announce_data("my server", True)
    parsed = parse_announce_data(raw)
    assert parsed == {"v": 1, "name": "my server", "auth": True}
    assert parse_announce_data(b"garbage") is None
    assert parse_announce_data(None) is None


def test_resolve_identity_success(monkeypatch):
    fake = object()
    monkeypatch.setattr(RNS.Transport, "has_path", staticmethod(lambda h: True))
    monkeypatch.setattr(RNS.Identity, "recall", staticmethod(lambda h: fake))
    assert resolve_identity(b"0" * 16, timeout=0.5) is fake


def test_resolve_identity_timeout(monkeypatch):
    calls = []
    monkeypatch.setattr(RNS.Transport, "has_path", staticmethod(lambda h: False))
    monkeypatch.setattr(
        RNS.Transport, "request_path", staticmethod(lambda h: calls.append(h))
    )
    with pytest.raises(ResolutionError):
        resolve_identity(b"0" * 16, timeout=0.2)
    assert calls
