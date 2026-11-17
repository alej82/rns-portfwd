from __future__ import annotations

from rns_portfwd.config import Service
from rns_portfwd.registry import ServiceRegistry

IDENTITY_A = bytes.fromhex("0123456789abcdef0123456789abcdef")
IDENTITY_B = bytes.fromhex("fedcba9876543210fedcba9876543210")


def make_registry(max_connections=2, list_password: str | None = "listpass"):
    services = [
        Service("127.0.0.1", 22, "ssh", "secret"),
        Service("127.0.0.1", 5432, "db", "dbpass"),
    ]
    return ServiceRegistry(services, list_password, max_connections)


def test_authenticate_and_probe():
    registry = make_registry()
    auth = registry.authenticate("ssh", "secret")
    assert auth is not None and auth.name == "ssh"
    assert registry.authenticate("ssh", "wrong") is None
    assert registry.authenticate("missing", "secret") is None
    assert registry.probe("db", "dbpass") == {
        "name": "db",
        "protocol": "tcp",
        "auth": True,
    }
    assert registry.probe("db", "wrong") is None
    assert registry.probe("missing", "wrong") is None


def test_list_public_and_password():
    registry = make_registry()
    assert registry.list_enabled is True
    assert registry.check_list_password("listpass") is True
    assert registry.check_list_password("wrong") is False
    public = registry.list_public()
    assert [item["name"] for item in public] == ["ssh", "db"]
    assert all("password" not in item and "host" not in item for item in public)


def test_list_disabled():
    registry = make_registry(list_password=None)
    assert registry.list_enabled is False
    assert registry.check_list_password("anything") is False


def test_connection_limits():
    registry = make_registry(max_connections=2)
    assert registry.acquire("ssh") is True
    assert registry.acquire("ssh") is True
    assert registry.acquire("ssh") is False
    assert registry.active_connections("ssh") == 2
    registry.release("ssh")
    assert registry.active_connections("ssh") == 1
    assert registry.acquire("ssh") is True
    registry.release("ssh")
    registry.release("ssh")
    registry.release("ssh")
    assert registry.active_connections("ssh") == 0


def make_public_registry(max_private=2, max_public=1):
    services = [
        Service("127.0.0.1", 22, "ssh", "secret"),
        Service("127.0.0.1", 80, "web", None),
    ]
    return ServiceRegistry(services, "listpass", max_private, max_public)


def test_public_service_accepts_any_credential():
    registry = make_public_registry()
    for password in ("", "anything", None):
        service = registry.authenticate("web", password)
        assert service is not None and service.name == "web"
    assert registry.authenticate("ssh", "") is None
    assert registry.authenticate("missing", "") is None
    assert registry.probe("web", "") == {
        "name": "web",
        "protocol": "tcp",
        "auth": False,
    }


def test_public_connection_limit_is_separate():
    registry = make_public_registry(max_private=2, max_public=1)
    assert registry.acquire("web") is True
    assert registry.acquire("web") is False
    assert registry.acquire("ssh") is True
    assert registry.acquire("ssh") is True
    assert registry.acquire("ssh") is False
    registry.release("web")
    assert registry.active_connections("web") == 0
    assert registry.acquire("web") is True


def make_allowlisted_registry(max_private=3, max_public=1):
    services = [
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
        Service("127.0.0.1", 443, "open", None),
    ]
    return ServiceRegistry(services, "listpass", max_private, max_public)


def test_private_service_requires_password_and_identity():
    registry = make_allowlisted_registry()
    allowed = registry.authenticate("ssh", "secret", IDENTITY_A)
    assert allowed is not None and allowed.name == "ssh"
    # Correct password with an unlisted identity is rejected.
    assert registry.authenticate("ssh", "secret", IDENTITY_B) is None
    # An allowed identity still needs the password.
    assert registry.authenticate("ssh", "wrong", IDENTITY_A) is None
    # Anonymous callers are rejected.
    assert registry.authenticate("ssh", "secret") is None
    assert registry.authenticate("ssh", "secret", None) is None


def test_identity_is_ignored_for_services_without_allow_list():
    registry = make_allowlisted_registry()
    assert registry.authenticate("open", "", IDENTITY_B) is not None
    assert registry.authenticate("open", "", None) is not None


def test_public_service_with_allow_list_ignores_password_but_checks_identity():
    registry = make_allowlisted_registry()
    for password in ("", "anything", None):
        service = registry.authenticate("web", password, IDENTITY_A)
        assert service is not None and service.name == "web"
    assert registry.authenticate("web", "", IDENTITY_B) is None
    assert registry.authenticate("web", "anything", None) is None
    assert registry.probe("web", "", IDENTITY_A) == {
        "name": "web",
        "protocol": "tcp",
        "auth": True,
    }
    assert registry.probe("web", "", IDENTITY_B) is None
    assert registry.probe("web", "", None) is None


def test_probe_reports_auth_for_allow_listed_private_service():
    registry = make_allowlisted_registry()
    assert registry.probe("ssh", "secret", IDENTITY_A) == {
        "name": "ssh",
        "protocol": "tcp",
        "auth": True,
    }
    assert registry.probe("ssh", "secret", IDENTITY_B) is None
    assert registry.probe("ssh", "wrong", IDENTITY_A) is None


def test_allow_listed_public_service_uses_service_limit():
    registry = make_allowlisted_registry(max_private=3, max_public=1)
    assert registry.acquire("web") is True
    assert registry.acquire("web") is True
    assert registry.acquire("web") is True
    assert registry.acquire("web") is False
    # An unrestricted public service still uses the public limit.
    assert registry.acquire("open") is True
    assert registry.acquire("open") is False
    registry.release("web")
    assert registry.active_connections("web") == 2
