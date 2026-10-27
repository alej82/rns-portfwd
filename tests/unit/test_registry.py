from __future__ import annotations

from rns_portfwd.config import Service
from rns_portfwd.registry import ServiceRegistry


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
