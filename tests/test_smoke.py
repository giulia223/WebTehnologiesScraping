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


@pytest.mark.parametrize("command", COMMANDS)
def test_stub_exits_with_error(command: str) -> None:
    result = runner.invoke(app, [command])
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


def test_cli_flag_reaches_config(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Config] = []

    def capture(command: str, config: Config) -> None:
        seen.append(config)

    monkeypatch.setattr("techdetect.cli._not_implemented", capture)
    result = runner.invoke(app, ["collect", "--concurrency", "4"])
    assert result.exit_code == 0
    assert seen[0].concurrency == 4
    assert seen[0].static_timeout_s == Config().static_timeout_s
