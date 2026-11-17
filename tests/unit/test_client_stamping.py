"""Client-side link stamping: flag parsing, propagation and configuration order."""

from __future__ import annotations

import logging

import pytest

from rns_portfwd.cli_client import build_parser, main
from rns_portfwd.client import PortfwdClient, run_list, run_probe
from rns_portfwd.constants import EXIT_CONFIG, EXIT_OK

SERVER_HASH = "0" * 32
SERVER_HASH_BYTES = bytes.fromhex(SERVER_HASH)
LOGGER = logging.getLogger("test.client_stamping")


class Logger:
    def __init__(self):
        self.messages: list[tuple[str, tuple]] = []

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


@pytest.fixture(autouse=True)
def quiet_logger(monkeypatch):
    logger = Logger()
    monkeypatch.setattr("rns_portfwd.cli_client.setup_logging", lambda verbosity=0: logger)
    return logger


@pytest.fixture
def no_reticulum(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Reticulum must not be initialised for this command")

    monkeypatch.setattr("rns_portfwd.discovery.init_reticulum", fail)
    monkeypatch.setattr("rns_portfwd.client.init_reticulum", fail)
    monkeypatch.setattr("rns_portfwd.client.configure_link_stamping", fail)
    return fail


class StopEvent:
    """Stops ``PortfwdClient.run`` after one pass without touching Reticulum."""

    def is_set(self) -> bool:
        return True

    def set(self) -> None:
        pass

    def wait(self, timeout: float | None = None) -> bool:
        return True


# --- parser ---------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["forward", "--server", SERVER_HASH, "--forward", "ssh:2222:pw"],
        ["list", "--server", SERVER_HASH],
        ["probe", "--server", SERVER_HASH, "--service", "ssh"],
    ],
)
def test_parser_defaults_leave_stamping_to_discovery(argv):
    args = build_parser().parse_args(argv)
    assert args.link_stamp_cost is None
    assert args.no_link_stamp is False


def test_parser_accepts_link_stamp_cost_before_subcommand():
    args = build_parser().parse_args(
        ["--link-stamp-cost", "12.5", "list", "--server", SERVER_HASH]
    )
    assert args.link_stamp_cost == 12.5
    assert args.no_link_stamp is False


def test_parser_accepts_link_stamp_cost_after_subcommand():
    args = build_parser().parse_args(
        ["probe", "--server", SERVER_HASH, "--service", "ssh", "--link-stamp-cost", "8"]
    )
    assert args.link_stamp_cost == 8.0


def test_parser_accepts_no_link_stamp_before_subcommand():
    args = build_parser().parse_args(
        ["--no-link-stamp", "forward", "--server", SERVER_HASH, "--forward", "ssh:2222:pw"]
    )
    assert args.no_link_stamp is True
    assert args.link_stamp_cost is None


def test_parser_accepts_no_link_stamp_after_subcommand():
    args = build_parser().parse_args(
        ["list", "--server", SERVER_HASH, "--no-link-stamp"]
    )
    assert args.no_link_stamp is True
    assert args.link_stamp_cost is None


# --- CLI propagation ------------------------------------------------------


@pytest.fixture
def recorded_client(monkeypatch):
    recorded: dict = {}

    class RecordingClient:
        def __init__(self, **kwargs):
            recorded.update(kwargs)

        def run(self):
            recorded["ran"] = True

        def stop(self):
            pass

    monkeypatch.setattr("rns_portfwd.cli_client.PortfwdClient", RecordingClient)
    return recorded


@pytest.fixture
def recorded_run_list(monkeypatch):
    recorded: dict = {}

    def fake_run_list(**kwargs):
        recorded.update(kwargs)

    monkeypatch.setattr("rns_portfwd.cli_client.run_list", fake_run_list)
    return recorded


@pytest.fixture
def recorded_run_probe(monkeypatch):
    recorded: dict = {}

    def fake_run_probe(**kwargs):
        recorded.update(kwargs)

    monkeypatch.setattr("rns_portfwd.cli_client.run_probe", fake_run_probe)
    return recorded


def test_forward_propagates_stamp_override(recorded_client):
    assert (
        main(
            [
                "--link-stamp-cost",
                "12",
                "forward",
                "--server",
                SERVER_HASH,
                "--forward",
                "ssh:2222:pw",
            ]
        )
        == EXIT_OK
    )
    assert recorded_client["link_stamp_cost"] == 12.0
    assert recorded_client["no_link_stamp"] is False


def test_forward_propagates_no_link_stamp(recorded_client):
    assert (
        main(
            [
                "forward",
                "--server",
                SERVER_HASH,
                "--forward",
                "ssh:2222:pw",
                "--no-link-stamp",
            ]
        )
        == EXIT_OK
    )
    assert recorded_client["link_stamp_cost"] is None
    assert recorded_client["no_link_stamp"] is True


def test_forward_defaults_propagate_none_and_false(recorded_client):
    assert (
        main(["forward", "--server", SERVER_HASH, "--forward", "ssh:2222:pw"])
        == EXIT_OK
    )
    assert recorded_client["link_stamp_cost"] is None
    assert recorded_client["no_link_stamp"] is False


def test_list_propagates_stamp_override(recorded_run_list):
    assert (
        main(["list", "--server", SERVER_HASH, "--link-stamp-cost", "8"])
        == EXIT_OK
    )
    assert recorded_run_list["link_stamp_cost"] == 8.0
    assert recorded_run_list["no_link_stamp"] is False


def test_list_propagates_no_link_stamp(recorded_run_list):
    assert (
        main(["list", "--server", SERVER_HASH, "--no-link-stamp"]) == EXIT_OK
    )
    assert recorded_run_list["link_stamp_cost"] is None
    assert recorded_run_list["no_link_stamp"] is True


def test_probe_propagates_stamp_override(recorded_run_probe):
    assert (
        main(
            [
                "probe",
                "--link-stamp-cost",
                "14",
                "--server",
                SERVER_HASH,
                "--service",
                "ssh",
            ]
        )
        == EXIT_OK
    )
    assert recorded_run_probe["link_stamp_cost"] == 14.0
    assert recorded_run_probe["no_link_stamp"] is False


def test_probe_defaults_propagate_none_and_false(recorded_run_probe):
    assert (
        main(["probe", "--server", SERVER_HASH, "--service", "ssh"]) == EXIT_OK
    )
    assert recorded_run_probe["link_stamp_cost"] is None
    assert recorded_run_probe["no_link_stamp"] is False


# --- CLI validation -------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        ["forward", "--server", SERVER_HASH, "--forward", "ssh:2222:pw"],
        ["list", "--server", SERVER_HASH],
        ["probe", "--server", SERVER_HASH, "--service", "ssh"],
    ],
)
def test_conflicting_flags_are_a_config_error(monkeypatch, command, recorded_client):
    assert (
        main(["--link-stamp-cost", "8", "--no-link-stamp", *command])
        == EXIT_CONFIG
    )
    assert recorded_client == {}


@pytest.mark.parametrize("cost", ["41", "-1", "nan"])
def test_out_of_range_cost_is_a_config_error(recorded_client, cost):
    assert (
        main(
            [
                "forward",
                "--server",
                SERVER_HASH,
                "--forward",
                "ssh:2222:pw",
                "--link-stamp-cost",
                cost,
            ]
        )
        == EXIT_CONFIG
    )
    assert recorded_client == {}


def test_non_numeric_cost_exits_with_config_code():
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "list",
                "--server",
                SERVER_HASH,
                "--link-stamp-cost",
                "not-a-number",
            ]
        )
    assert excinfo.value.code == EXIT_CONFIG


# --- configure_link_stamping invocation -----------------------------------


def _connect_once_with_stubs(client, monkeypatch, on_manager=None):
    class OpenedLink:
        def get_channel(self):
            return object()

    class Manager:
        def __init__(self, identity, **kwargs):
            if on_manager is not None:
                on_manager()

        def open(self):
            return OpenedLink()

    class MuxFactory:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("rns_portfwd.client.LinkManager", Manager)
    monkeypatch.setattr("rns_portfwd.client.Mux", MuxFactory)
    monkeypatch.setattr("rns_portfwd.client.register_message_types", lambda channel: None)
    client._identity = object()
    client._connect_once()


def test_connect_once_configures_stamping_before_link(monkeypatch):
    order: list[str] = []
    configured: list[tuple] = []

    def fake_configure(server_hash, override, disabled, logger):
        order.append("stamp")
        configured.append((server_hash, override, disabled, logger))
        return 12.5

    monkeypatch.setattr("rns_portfwd.client.configure_link_stamping", fake_configure)
    client = PortfwdClient(
        server_hash=SERVER_HASH_BYTES,
        forwards=[],
        bind="127.0.0.1",
        logger=LOGGER,
        link_stamp_cost=12.5,
    )

    _connect_once_with_stubs(
        client, monkeypatch, on_manager=lambda: order.append("manager")
    )

    assert order == ["stamp", "manager"]
    assert configured == [(SERVER_HASH_BYTES, 12.5, False, LOGGER)]


def test_connect_once_passes_disable_flag(monkeypatch):
    configured: list[tuple] = []

    def fake_configure(server_hash, override, disabled, logger):
        configured.append((server_hash, override, disabled))

    monkeypatch.setattr("rns_portfwd.client.configure_link_stamping", fake_configure)
    client = PortfwdClient(
        server_hash=SERVER_HASH_BYTES,
        forwards=[],
        bind="127.0.0.1",
        logger=LOGGER,
        no_link_stamp=True,
    )

    _connect_once_with_stubs(client, monkeypatch)

    assert configured == [(SERVER_HASH_BYTES, None, True)]


class Manager:
    def __init__(self, identity, **kwargs):
        ORDER.append("manager")

    def open(self):
        ORDER.append("open")
        return object()

    def close(self):
        ORDER.append("close")


ORDER: list[str] = []


@pytest.fixture
def stub_command_runtime(monkeypatch):
    ORDER.clear()
    configured: list[tuple] = []

    def fake_init(config_dir, verbosity=0):
        ORDER.append("init")

    def fake_resolve(destination_hash, timeout):
        ORDER.append("resolve")
        return object()

    def fake_configure(server_hash, override, disabled, logger):
        ORDER.append("stamp")
        configured.append((server_hash, override, disabled))
        return 0.0

    monkeypatch.setattr("rns_portfwd.client.init_reticulum", fake_init)
    monkeypatch.setattr("rns_portfwd.client.resolve_identity", fake_resolve)
    monkeypatch.setattr("rns_portfwd.client.configure_link_stamping", fake_configure)
    monkeypatch.setattr("rns_portfwd.client.LinkManager", Manager)
    monkeypatch.setattr(
        "rns_portfwd.client.RNS.Reticulum.exit_handler",
        lambda: ORDER.append("exit"),
    )
    return configured


def test_run_list_configures_stamping_after_resolve(monkeypatch, stub_command_runtime):
    def fake_list_request(link, path, data, timeout):
        ORDER.append("request")
        return {"ok": True, "services": []}

    monkeypatch.setattr("rns_portfwd.client.link_request", fake_list_request)

    run_list(SERVER_HASH, None, LOGGER, None, link_stamp_cost=8.0)

    assert ORDER == ["init", "resolve", "stamp", "manager", "open", "request", "close", "exit"]
    assert stub_command_runtime == [(SERVER_HASH_BYTES, 8.0, False)]


def test_run_list_passes_no_link_stamp(monkeypatch, stub_command_runtime):
    def fake_list_request(link, path, data, timeout):
        return {"ok": True, "services": []}

    monkeypatch.setattr("rns_portfwd.client.link_request", fake_list_request)

    run_list(SERVER_HASH, None, LOGGER, None, no_link_stamp=True)

    assert ORDER.index("stamp") < ORDER.index("manager")
    assert stub_command_runtime == [(SERVER_HASH_BYTES, None, True)]


def test_run_probe_configures_stamping_after_resolve(monkeypatch, stub_command_runtime):
    def fake_probe_request(link, service, password, timeout, client_identity=None):
        ORDER.append("probe")
        return {"ok": True, "service": {"name": service, "protocol": "tcp"}}

    monkeypatch.setattr("rns_portfwd.client.probe_request", fake_probe_request)

    run_probe(SERVER_HASH, "ssh", "pw", LOGGER, None, link_stamp_cost=14.0)

    assert ORDER == ["init", "resolve", "stamp", "manager", "open", "probe", "close", "exit"]
    assert stub_command_runtime == [(SERVER_HASH_BYTES, 14.0, False)]


def test_run_probe_without_flags_configures_discovery(monkeypatch, stub_command_runtime):
    def fake_probe_request(link, service, password, timeout, client_identity=None):
        return {"ok": True, "service": {"name": service, "protocol": "tcp"}}

    monkeypatch.setattr("rns_portfwd.client.probe_request", fake_probe_request)

    run_probe(SERVER_HASH, "ssh", "pw", LOGGER, None)

    assert stub_command_runtime == [(SERVER_HASH_BYTES, None, False)]


# --- identity subcommand is unaffected ------------------------------------


def test_identity_command_never_configures_stamping(
    monkeypatch, tmp_path, capsys, no_reticulum
):
    path = tmp_path / "client.id"
    assert main(["--link-stamp-cost", "8", "identity", "--identity", str(path)]) == EXIT_OK
    output = capsys.readouterr().out.strip()
    assert len(output) == 32
    bytes.fromhex(output)


def test_identity_command_validates_stamp_flags(tmp_path, no_reticulum):
    path = tmp_path / "client.id"
    assert (
        main(["--link-stamp-cost", "999", "identity", "--identity", str(path)])
        == EXIT_CONFIG
    )
    assert (
        main(
            [
                "--link-stamp-cost",
                "8",
                "--no-link-stamp",
                "identity",
                "--identity",
                str(path),
            ]
        )
        == EXIT_CONFIG
    )
