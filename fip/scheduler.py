"""Run FIP's persistent source schedules as a separate worker process.

Start with ``python -m fip.scheduler`` beside the web process. Each due run is
claimed and recorded in SQLite before retrieval, so multiple workers do not
enqueue the same source interval concurrently.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from .ingestion import scheduler_loop


def main() -> None:
    base_dir = Path(__file__).resolve().parent.parent
    database = Path(os.environ.get("FIP_DATABASE", str(base_dir / "instance" / "fip.sqlite3"))).expanduser()
    poll_seconds = int(os.environ.get("FIP_SCHEDULER_POLL_SECONDS", "60"))
    logging.basicConfig(level=os.environ.get("FIP_SCHEDULER_LOG_LEVEL", "INFO"))
    logging.getLogger(__name__).info("FIP scheduler polling %s every %s seconds", database, poll_seconds)
    scheduler_loop(database, poll_seconds)


if __name__ == "__main__":
    main()
