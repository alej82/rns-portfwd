from __future__ import annotations

import stat

import pytest

from rns_portfwd.cli_client import DEFAULT_CLIENT_IDENTITY, build_parser, main
from rns_portfwd.constants import EXIT_CONFIG, EXIT_OK

SERVER_HASH = "0" * 32


@pytest.fixture
def no_reticulum(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Reticulum must not be initialised for this command")

    monkeypatch.setattr("rns_portfwd.discovery.init_reticulum", fail)
    monkeypatch.setattr("rns_portfwd.client.init_reticulum", fail)
    return fail


class FakeIdentity:
    hash = bytes.fromhex("ab" * 16)


@pytest.fixture(autouse=True)
def quiet_logger(monkeypatch):
    class Logger:
        def __init__(self):
            self.messages = []

        def _record(self, level, *args, **kwargs):
            self.messages.append((level, args))

        def debug(self, *args, **kwargs):
            self._record("debug", *args, **kwargs)

        def info(self, *args, **kwargs):
            self._record("info", *args, **kwargs)

        def warning(self, *args, **kwargs):
            self._record("warning", *args, **kwargs)

        def error(self, *args, **kwargs):
            self._record("error", *args, **kwargs)

    logger = Logger()
    monkeypatch.setattr("rns_portfwd.cli_client.setup_logging", lambda verbosity=0: logger)
    return logger


@pytest.fixture
def identity_sentinel(monkeypatch):
    sentinel = FakeIdentity()
    calls: list[str] = []

    def fake_loader(path):
        calls.append(path)
        return sentinel

    monkeypatch.setattr("rns_portfwd.cli_client.load_or_create_identity", fake_loader)
    return sentinel, calls


def test_parser_accepts_identity_before_subcommand():
    args = build_parser().parse_args(
        ["--identity", "/tmp/client.id", "list", "--server", SERVER_HASH]
    )
    assert args.identity == "/tmp/client.id"


def test_parser_accepts_identity_after_subcommand():
    args = build_parser().parse_args(
        [
            "forward",
            "--server",
            SERVER_HASH,
            "--forward",
            "ssh:2222:pw",
            "--identity",
            "/tmp/client.id",
        ]
    )
    assert args.identity == "/tmp/client.id"


@pytest.mark.parametrize(
    "argv",
    [
        ["forward", "--server", SERVER_HASH, "--forward", "ssh:2222:pw"],
        ["list", "--server", SERVER_HASH],
        ["probe", "--server", SERVER_HASH, "--service", "ssh"],
    ],
)
def test_parser_default_is_anonymous(argv):
    assert build_parser().parse_args(argv).identity is None


def test_default_identity_path_constant():
    assert DEFAULT_CLIENT_IDENTITY == "~/.config/rns-portfwd/client.id"


def test_identity_subcommand_prints_only_the_hash(monkeypatch, tmp_path, capsys, no_reticulum):
    path = tmp_path / "client.id"
    assert main(["identity", "--identity", str(path)]) == EXIT_OK
    out = capsys.readouterr().out
    assert out.endswith("\n")
    assert out.count("\n") == 1
    text = out.strip()
    assert len(text) == 32
    bytes.fromhex(text)


def test_identity_subcommand_creates_file_with_owner_only_permissions(
    monkeypatch, tmp_path, capsys, no_reticulum
):
    path = tmp_path / "nested" / "client.id"
    assert main(["identity", "--identity", str(path)]) == EXIT_OK
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_identity_subcommand_reuses_existing_file(monkeypatch, tmp_path, capsys, no_reticulum):
    path = tmp_path / "client.id"
    assert main(["identity", "--identity", str(path)]) == EXIT_OK
    first = capsys.readouterr().out.strip()
    assert main(["identity", "--identity", str(path)]) == EXIT_OK
    second = capsys.readouterr().out.strip()
    assert first == second


def test_identity_subcommand_uses_default_path(monkeypatch, tmp_path, capsys, no_reticulum):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert main(["identity"]) == EXIT_OK
    text = capsys.readouterr().out.strip()
    assert len(text) == 32
    assert (tmp_path / ".config" / "rns-portfwd" / "client.id").exists()


def test_identity_subcommand_accepts_flag_before_subcommand(
    monkeypatch, tmp_path, capsys, no_reticulum
):
    path = tmp_path / "before.id"
    assert main(["--identity", str(path), "identity"]) == EXIT_OK
    assert len(capsys.readouterr().out.strip()) == 32
    assert path.exists()


def test_identity_subcommand_does_not_read_secret_source(
    monkeypatch, tmp_path, capsys, no_reticulum
):
    def fail(*args, **kwargs):
        raise AssertionError("the identity command must not read secrets/stdin")

    monkeypatch.setattr("rns_portfwd.cli_client.read_secret_source", fail)
    path = tmp_path / "client.id"
    assert main(["--password-stdin", "identity", "--identity", str(path)]) == EXIT_OK
    assert len(capsys.readouterr().out.strip()) == 32


def test_forward_propagates_identity(monkeypatch, tmp_path, identity_sentinel):
    sentinel, calls = identity_sentinel
    recorded: dict = {}

    class RecordingClient:
        def __init__(self, **kwargs):
            recorded.update(kwargs)

        def run(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr("rns_portfwd.cli_client.PortfwdClient", RecordingClient)
    path = str(tmp_path / "client.id")
    exit_code = main(
        [
            "forward",
            "--server",
            SERVER_HASH,
            "--forward",
            "ssh:2222:pw",
            "--identity",
            path,
        ]
    )
    assert exit_code == EXIT_OK
    assert calls == [path]
    assert recorded["client_identity"] is sentinel


def test_list_propagates_identity(monkeypatch, tmp_path, identity_sentinel):
    sentinel, calls = identity_sentinel
    recorded: dict = {}

    def fake_run_list(**kwargs):
        recorded.update(kwargs)

    monkeypatch.setattr("rns_portfwd.cli_client.run_list", fake_run_list)
    path = str(tmp_path / "client.id")
    exit_code = main(["list", "--server", SERVER_HASH, "--identity", path])
    assert exit_code == EXIT_OK
    assert calls == [path]
    assert recorded["client_identity"] is sentinel


def test_probe_propagates_identity(monkeypatch, tmp_path, identity_sentinel):
    sentinel, calls = identity_sentinel
    recorded: dict = {}

    def fake_run_probe(**kwargs):
        recorded.update(kwargs)

    monkeypatch.setattr("rns_portfwd.cli_client.run_probe", fake_run_probe)
    path = str(tmp_path / "client.id")
    exit_code = main(
        ["probe", "--server", SERVER_HASH, "--service", "ssh", "--identity", path]
    )
    assert exit_code == EXIT_OK
    assert calls == [path]
    assert recorded["client_identity"] is sentinel


def test_empty_identity_path_is_rejected(monkeypatch):
    def fail(path):
        raise AssertionError("identity must not be loaded for an empty path")

    monkeypatch.setattr("rns_portfwd.cli_client.load_or_create_identity", fail)
    assert main(["list", "--server", SERVER_HASH, "--identity", ""]) == EXIT_CONFIG


def test_identity_hash_is_logged_only_at_debug(monkeypatch, tmp_path, quiet_logger):
    sentinel = FakeIdentity()
    monkeypatch.setattr(
        "rns_portfwd.cli_client.load_or_create_identity", lambda path: sentinel
    )

    class RecordingClient:
        def __init__(self, **kwargs):
            pass

        def run(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr("rns_portfwd.cli_client.PortfwdClient", RecordingClient)
    assert (
        main(
            [
                "forward",
                "--server",
                SERVER_HASH,
                "--forward",
                "ssh:2222:pw",
                "--identity",
                str(tmp_path / "client.id"),
            ]
        )
        == EXIT_OK
    )
    levels = [level for level, _ in quiet_logger.messages]
    assert "info" not in levels
    assert any(
        level == "debug" and sentinel.hash.hex() in str(message)
        for level, message in quiet_logger.messages
    )


def test_forward_without_identity_stays_anonymous(monkeypatch):
    def fail(path):
        raise AssertionError("identity must not be loaded without --identity")

    monkeypatch.setattr("rns_portfwd.cli_client.load_or_create_identity", fail)
    recorded: dict = {}

    class RecordingClient:
        def __init__(self, **kwargs):
            recorded.update(kwargs)

        def run(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr("rns_portfwd.cli_client.PortfwdClient", RecordingClient)
    exit_code = main(
        ["forward", "--server", SERVER_HASH, "--forward", "ssh:2222:pw"]
    )
    assert exit_code == EXIT_OK
    assert recorded["client_identity"] is None
