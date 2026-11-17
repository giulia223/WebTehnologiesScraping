from pathlib import Path

import pytest
from typer.testing import CliRunner

from techdetect import __version__
from techdetect.cli import app
from techdetect.config import Config

runner = CliRunner()
COMMANDS = ["collect", "detect", "evaluate", "report"]


def test_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in COMMANDS:
        assert command in result.output


@pytest.fixture
def tiny_input(tmp_path: Path) -> Path:
    path = tmp_path / "domains.txt"
    path.write_text("example.com\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("command", COMMANDS)
def test_stub_exits_with_error(command: str, tiny_input: Path) -> None:
    # collect loads its input before reaching the stub; give it a known small file.
    args = [command, "--input", str(tiny_input)] if command == "collect" else [command]
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "not implemented yet" in result.output


def test_config_defaults() -> None:
    config = Config()
    assert config.raw_dir == Path("data/raw")
    assert config.concurrency == 10
    assert config.user_agent.startswith(f"techdetect/{__version__} ")


def test_override_replaces_only_given_fields() -> None:
    config = Config().with_overrides(concurrency=3, raw_dir=None)
    assert config.concurrency == 3
    assert config.raw_dir == Config().raw_dir  # None = flag not passed, default kept


def test_cli_flag_reaches_config(monkeypatch: pytest.MonkeyPatch, tiny_input: Path) -> None:
    seen: list[Config] = []

    def capture(command: str, config: Config) -> None:
        seen.append(config)

    monkeypatch.setattr("techdetect.cli._not_implemented", capture)
    result = runner.invoke(app, ["collect", "--concurrency", "4", "--input", str(tiny_input)])
    assert result.exit_code == 0
    assert seen[0].concurrency == 4
    assert seen[0].static_timeout_s == Config().static_timeout_s


def test_collect_rejects_unimplemented_step(tiny_input: Path) -> None:
    result = runner.invoke(app, ["collect", "--only", "screenshots", "--input", str(tiny_input)])
    assert result.exit_code == 1
    assert "implemented steps are ['browser', 'dns', 'static', 'tls', 'wellknown']" in result.output


def test_collect_only_dns_runs_the_dns_collector(
    monkeypatch: pytest.MonkeyPatch, tiny_input: Path
) -> None:
    received: list[list[str]] = []

    async def fake_collect(domains, config):  # no network: record what would be probed
        received.append([d.host for d in domains])
        return [
            {
                "domain": d.host,
                "status": "ok",
                "queries": [{"status": "ok"}, {"status": "nxdomain"}],
            }
            for d in domains
        ]

    monkeypatch.setattr("techdetect.cli.collect_dns", fake_collect)
    result = runner.invoke(app, ["collect", "--only", "dns", "--input", str(tiny_input)])

    assert result.exit_code == 0, result.output
    assert len(received) == 1
    assert "1/2 queries answered" in result.output


def test_collect_only_static_respects_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    received: list[list[str]] = []

    async def fake_collect(domains, config):  # no network: record what would be fetched
        received.append([d.host for d in domains])
        return [
            {"domain": d.host, "status": "ok", "final_url": f"https://{d.host}/"} for d in domains
        ]

    monkeypatch.setattr("techdetect.cli.collect_static", fake_collect)
    path = tmp_path / "three.txt"
    path.write_text("a.com\nb.com\nc.com\n", encoding="utf-8")

    result = runner.invoke(
        app, ["collect", "--only", "static", "--limit", "2", "--input", str(path)]
    )

    assert result.exit_code == 0, result.output
    assert received == [["a.com", "b.com"]]
    assert "static: 2/2 ok" in result.output
