"""Run one collector over many domains: bounded concurrency, one crash never stops the rest."""

import asyncio
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

from techdetect.domains import Domain

Work = Callable[[Domain], Awaitable[dict[str, Any]]]
# Called as each domain finishes: (domain, result, duration in ms). The collect command
# uses it to write the run log as the run goes, so an interrupted run still shows progress.
OnResult = Callable[[Domain, dict[str, Any], int], None]


async def run_per_domain(
    domains: list[Domain], concurrency: int, work: Work, on_result: OnResult | None = None
) -> list[dict[str, Any]]:
    """Call `work` for every domain, at most `concurrency` at a time, results in input order."""
    # The semaphore is the politeness limit: never more than `concurrency` sites at once.
    semaphore = asyncio.Semaphore(concurrency)

    async def one(domain: Domain) -> dict[str, Any]:
        async with semaphore:
            # Timed after the semaphore: the duration is the work, not the wait for a slot.
            start = time.perf_counter()
            try:
                result = await work(domain)
            except Exception as exc:  # a bug on one site must not stop the others
                print(f"{domain.host}: unexpected error: {exc!r}", file=sys.stderr)
                result = {"domain": domain.host, "status": "crashed", "error": repr(exc)}
            if on_result is not None:
                on_result(domain, result, round((time.perf_counter() - start) * 1000))
            return result

    return await asyncio.gather(*(one(d) for d in domains))
