"""Tests for the full collect run. Steps are replaced by fakes that write JSON files:
no network, no browser."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from techdetect.cli import app
from techdetect.collect.run import (
    RUN_COMPLETE,
    RUN_LOG,
    STEP_FILES,
    STEPS,
    FinishedRunError,
    fill_final_url_differs,
    parse_steps,
    run_collect,
    step_outcome,
)
from techdetect.collect.runner import run_per_domain
from techdetect.collect.storage import domain_dir, write_json_atomic
from techdetect.config import Config
from techdetect.domains import Domain

DOMAINS = [Domain(1, "a.com", "a.com"), Domain(2, "b.com", "b.com")]


def fake_step(
    step: str, results: dict[str, dict[str, Any]] | None = None, calls: list | None = None
):
    """A collector that writes `<step>.json` per domain; a result with "raise" crashes."""

    async def collect(domains: list[Domain], config: Config, on_result=None):
        if calls is not None:
            calls.append(step)

        async def work(domain: Domain) -> dict[str, Any]:
            result = (results or {}).get(domain.host, {"domain": domain.host, "status": "ok"})
            if result.get("raise"):
                raise RuntimeError("boom")
            write_json_atomic(domain_dir(config.raw_dir, domain.host) / STEP_FILES[step], result)
            return result

        return await run_per_domain(domains, config.concurrency, work, on_result)

    return collect


def fakes(calls: list | None = None, **results: dict[str, dict[str, Any]]):
    return {step: fake_step(step, results.get(step), calls) for step in STEPS}


def run(tmp_path: Path, steps: list[str] | None = None, overwrite: bool = False, **results):
    config = Config(raw_dir=tmp_path / "raw")
    return asyncio.run(
        run_collect(DOMAINS, config, steps or list(STEPS), fakes(**results), overwrite=overwrite)
    )


# ------------------------------------------------------------ small pieces


def test_parse_steps_keeps_run_order_and_rejects_unknown() -> None:
    assert parse_steps(None) == list(STEPS)
    assert parse_steps("browser, static") == ["static", "browser"]  # static must come first
    with pytest.raises(ValueError, match="screenshots"):
        parse_steps("static,screenshots")


@pytest.mark.parametrize(
    ("step", "result", "expected"),
    [
        ("static", {"status": "failed", "error_category": "dns_fail"}, ("failed", "dns_fail")),
        ("browser", {"status": "ok", "error_category": None}, ("ok", None)),
        ("tls", {"status": "failed", "error_category": "conn_refused"}, ("failed", "conn_refused")),
        (
            "dns",
            {"status": "failed", "queries": [{"status": "nxdomain"}] * 3 + [{"status": "timeout"}]},
            ("failed", "nxdomain"),
        ),
        (
            "wellknown",
            {"status": "failed", "robots": {"error_category": "timeout"}},
            ("failed", "timeout"),
        ),
        ("static", {"status": "crashed", "error": "boom"}, ("crashed", None)),
    ],
)
def test_step_outcome(step: str, result: dict, expected: tuple) -> None:
    assert step_outcome(step, result) == expected


def test_final_url_differs_is_filled_when_both_steps_reached_a_page(tmp_path: Path) -> None:
    folder = tmp_path / "a.com"
    write_json_atomic(folder / "static.json", {"status": "ok", "final_url": "https://a.com/"})
    write_json_atomic(
        folder / "browser.json",
        {"status": "ok", "final_url": "https://a.com/en/", "final_url_differs_from_static": None},
    )
    fill_final_url_differs(tmp_path, "a.com")
    browser = json.loads((folder / "browser.json").read_text(encoding="utf-8"))
    assert browser["final_url_differs_from_static"] is True

    write_json_atomic(folder / "static.json", {"status": "failed", "final_url": None})
    fill_final_url_differs(tmp_path, "a.com")
    browser = json.loads((folder / "browser.json").read_text(encoding="utf-8"))
    assert browser["final_url_differs_from_static"] is None  # no comparison possible


# ------------------------------------------------------------ a whole run


def test_run_writes_log_and_completion_marker_from_disk(tmp_path: Path) -> None:
    complete = run(
        tmp_path,
        static={"b.com": {"domain": "b.com", "status": "failed", "error_category": "dns_fail"}},
    )
    out = tmp_path / "raw"

    marker = json.loads((out / RUN_COMPLETE).read_text(encoding="utf-8"))
    assert marker == complete
    assert marker["steps_run_now"] == list(STEPS)
    assert set(marker["steps"]) == set(STEPS)
    assert marker["counts"]["static"] == {
        "ok": 1,
        "failed": 1,
        "missing": 0,
        "by_category": {"dns_fail": 1},
    }
    assert set(marker["avg_seconds_per_domain"]) == set(STEPS)

    log = [json.loads(line) for line in (out / RUN_LOG).read_text(encoding="utf-8").splitlines()]
    assert len(log) == len(STEPS) * len(DOMAINS)
    failed = next(line for line in log if line["domain"] == "b.com" and line["step"] == "static")
    assert failed["status"] == "failed"
    assert failed["error_category"] == "dns_fail"
    assert set(failed) == {
        "run_started_at",
        "domain",
        "step",
        "status",
        "duration_ms",
        "error_category",
    }


def test_second_run_on_a_finished_folder_stops_without_overwrite(tmp_path: Path) -> None:
    run(tmp_path)
    before = (tmp_path / "raw" / RUN_COMPLETE).read_text(encoding="utf-8")

    with pytest.raises(FinishedRunError, match="--overwrite"):
        run(tmp_path)
    assert (tmp_path / "raw" / RUN_COMPLETE).read_text(encoding="utf-8") == before


def test_cli_second_collect_stops_without_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("techdetect.cli._collectors", lambda: fakes(calls))
    domains = tmp_path / "domains.txt"
    domains.write_text("a.com\nb.com\n", encoding="utf-8")
    args = ["collect", "--input", str(domains), "--out", str(tmp_path / "raw")]
    runner = CliRunner()

    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    assert "counts read from disk" in first.output

    second = runner.invoke(app, args)
    assert second.exit_code == 1
    assert "already holds a finished run" in second.output
    assert len(calls) == len(STEPS)  # nothing ran the second time

    third = runner.invoke(app, [*args, "--overwrite", "--only", "dns"])
    assert third.exit_code == 0, third.output
    assert calls[-1] == "dns"


def test_rerunning_one_step_keeps_the_other_steps_files(tmp_path: Path) -> None:
    run(tmp_path)
    out = tmp_path / "raw"
    static_file = out / "a.com" / "static.json"
    static_before = static_file.read_text(encoding="utf-8")
    first = json.loads((out / RUN_COMPLETE).read_text(encoding="utf-8"))

    second = run(
        tmp_path,
        steps=["browser"],
        overwrite=True,
        browser={"a.com": {"domain": "a.com", "status": "failed", "error_category": "timeout"}},
    )

    assert static_file.read_text(encoding="utf-8") == static_before
    assert second["steps_run_now"] == ["browser"]
    # Every step is still listed, each with when it ran; static kept its first-run dates.
    assert second["steps"]["static"] == first["steps"]["static"]
    assert second["steps"]["browser"]["started_at"] >= first["steps"]["browser"]["started_at"]
    assert second["counts"]["browser"]["by_category"] == {"timeout": 1}
    assert second["previous_runs"][0]["started_at"] == first["started_at"]


def test_crashed_domain_removes_its_stale_file_and_counts_as_missing(tmp_path: Path) -> None:
    run(tmp_path)  # a.com and b.com both have dns.json now
    complete = run(tmp_path, steps=["dns"], overwrite=True, dns={"b.com": {"raise": True}})
    assert not (tmp_path / "raw" / "b.com" / "dns.json").exists()
    assert complete["counts"]["dns"]["missing"] == 1
    log = (tmp_path / "raw" / RUN_LOG).read_text(encoding="utf-8")
    assert '"status": "crashed"' in log


def test_interrupted_overwrite_leaves_the_folder_unfinished(tmp_path: Path) -> None:
    run(tmp_path)

    async def broken(domains, config, on_result=None):
        raise KeyboardInterrupt  # e.g. Ctrl+C in the middle of a step

    collectors = fakes()
    collectors["dns"] = broken
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(
            run_collect(
                DOMAINS, Config(raw_dir=tmp_path / "raw"), ["dns"], collectors, overwrite=True
            )
        )
    assert not (tmp_path / "raw" / RUN_COMPLETE).exists()
