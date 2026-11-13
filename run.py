"""
L7 — daily runner. One command: discover new jobs, then refresh the dashboard.
This is what launchd fires each morning.

    python3 run.py

Resilient: if discovery fails (rate limit, network), it still regenerates the
dashboard from whatever is already in the store. Everything is logged to run.log.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import dashboard
import discover
import fit
import jds
import fill
import inbox
import postings

LOG = Path(__file__).parent / "run.log"
logging.basicConfig(
    filename=LOG,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


async def main() -> None:
    logging.info("=== jobpilot run start ===")
    try:
        result = await discover.discover()
        logging.info("discovery ok: %s", result)
    except Exception:
        logging.exception("discovery FAILED — refreshing dashboard from existing store anyway")
    try:
        # read each new job's description (free: ATS feeds, else the page), then a cheap fit check against the
        # targeting in profile.md — jobs that aren't the candidate's kind of work go to skipped before any apply agent runs
        logging.info("descriptions: %s", await jds.fetch(statuses=("new",)))
        logging.info("fit: %s", await fit.check())
    except Exception:
        logging.exception("description/fit step FAILED — filling every new job as before")
    try:
        # auto-fill: read + draft every job still in status 'new'
        summary = await fill.fill()
        logging.info("fill ok: %s", summary)
    except Exception:
        logging.exception("fill FAILED — continuing to dashboard")
    try:
        # postings close within days — catch the dead ones before you spend time reviewing them
        logging.info("postings: %s", postings.check())
        logging.info("postings (Claude): %s", await postings.claude_check())
    except Exception:
        logging.exception("posting check FAILED")
    try:
        # the candidate's job emails: what the candidate submitted, replies, rejections, interviews (read-only; asks the candidate when unsure)
        logging.info("inbox: %s", await inbox.check())
    except Exception:
        logging.exception("inbox check FAILED (if Gmail's approval expired: Reconnect Gmail on the dashboard)")
    try:
        out = dashboard.build()
        logging.info("dashboard refreshed: %s", out)
    except Exception:
        logging.exception("dashboard FAILED")
    logging.info("=== jobpilot run done ===")


if __name__ == "__main__":
    asyncio.run(main())
