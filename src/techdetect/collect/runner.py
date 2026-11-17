"""Run one collector over many domains: bounded concurrency, one crash never stops the rest."""

import asyncio
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from techdetect.domains import Domain

Work = Callable[[Domain], Awaitable[dict[str, Any]]]


async def run_per_domain(
    domains: list[Domain], concurrency: int, work: Work
) -> list[dict[str, Any]]:
    """Call `work` for every domain, at most `concurrency` at a time, results in input order."""
    # The semaphore is the politeness limit: never more than `concurrency` sites at once.
    semaphore = asyncio.Semaphore(concurrency)

    async def one(domain: Domain) -> dict[str, Any]:
        async with semaphore:
            try:
                return await work(domain)
            except Exception as exc:  # a bug on one site must not stop the others
                print(f"{domain.host}: unexpected error: {exc!r}", file=sys.stderr)
                return {"domain": domain.host, "status": "crashed", "error": repr(exc)}

    return await asyncio.gather(*(one(d) for d in domains))
