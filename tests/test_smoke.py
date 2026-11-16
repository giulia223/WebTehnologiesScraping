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
