from pathlib import Path

import pytest
from typer.testing import CliRunner

from techdetect import __version__
from techdetect.cli import app
from techdetect.collect.runner import run_per_domain
from techdetect.config import Config

runner = CliRunner()
COMMANDS = ["collect", "rules", "detect", "evaluate", "report"]
STUBS = ["detect", "evaluate", "report"]


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


@pytest.mark.parametrize("command", STUBS)
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


def fake(results_for):
    """A collector that returns canned results through the real runner: no network."""
    received: list[list[str]] = []

    async def collect(domains, config, on_result=None):
        received.append([d.host for d in domains])

        async def work(domain):
            return results_for(domain.host)

        return await run_per_domain(domains, config.concurrency, work, on_result)

    collect.received = received  # type: ignore[attr-defined]
    return collect


def test_cli_flag_reaches_config(
    monkeypatch: pytest.MonkeyPatch, tiny_input: Path, tmp_path: Path
) -> None:
    seen: list[Config] = []

    async def capture(domains, config, steps, collectors, **kwargs):
        seen.append(config)
        return {"domains": 1, "counts": {}, "steps_run_now": steps, "avg_seconds_per_domain": {}}

    monkeypatch.setattr("techdetect.cli.run_collect", capture)
    args = ["collect", "--concurrency", "4", "--input", str(tiny_input), "--out", str(tmp_path)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert seen[0].concurrency == 4
    assert seen[0].browser_concurrency == 4
    assert seen[0].raw_dir == tmp_path
    assert seen[0].static_timeout_s == Config().static_timeout_s


def test_collect_rejects_unknown_step(tiny_input: Path) -> None:
    result = runner.invoke(app, ["collect", "--only", "screenshots", "--input", str(tiny_input)])
    assert result.exit_code == 1
    assert "unknown step(s) ['screenshots']" in result.output


def test_collect_only_dns_runs_the_dns_collector(
    monkeypatch: pytest.MonkeyPatch, tiny_input: Path, tmp_path: Path
) -> None:
    dns = fake(
        lambda host: {
            "domain": host,
            "status": "ok",
            "queries": [{"status": "ok"}, {"status": "nxdomain"}],
        }
    )
    monkeypatch.setattr("techdetect.cli.collect_dns", dns)
    args = ["collect", "--only", "dns", "--input", str(tiny_input), "--out", str(tmp_path)]
    result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    assert dns.received == [["example.com"]]
    assert "1/2 queries answered" in result.output


def test_collect_respects_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    static = fake(lambda host: {"domain": host, "status": "ok", "final_url": f"https://{host}/"})
    monkeypatch.setattr("techdetect.cli.collect_static", static)
    path = tmp_path / "three.txt"
    path.write_text("a.com\nb.com\nc.com\n", encoding="utf-8")

    args = ["collect", "--only", "static", "--limit", "2", "--input", str(path)]
    result = runner.invoke(app, [*args, "--out", str(tmp_path / "raw")])

    assert result.exit_code == 0, result.output
    assert static.received == [["a.com", "b.com"]]
    assert "https://a.com/" in result.output
