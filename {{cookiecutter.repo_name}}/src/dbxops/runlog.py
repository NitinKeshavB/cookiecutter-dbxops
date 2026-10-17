"""
Structured run logging with loguru: JSON on stdout plus the run's events in ``md_run_event``.

``configure_run_logging`` is called once by a job task. Every line is JSON (``serialize=True``) carrying the run's
context (``run_id``, ``target``, ``repo_id``, ``git_sha`` and anything else passed in), so driver logs can be searched
by run. Secrets are masked by a patcher before any sink sees a record. ``RunEventBuffer`` keeps the events of the run
in memory; the task writes them to ``md_run_event`` at the end with the rest of its operational facts. The sinks
only format and collect, so logging itself cannot fail a run.

Inside declarative pipelines the event log stays the record; this is for framework code paths (job tasks, landing
jobs, CLIs).
"""

from __future__ import annotations

import re
import sys
from datetime import (
    datetime,
    timezone,
)
from typing import (
    Any,
    Mapping,
    TextIO,
)

from loguru import logger

from dbxops.metastore.records import RunEventRecord

REDACTED = "***"
_SECRET_KEY = re.compile(r"(secret|token|password|passwd|credential|api_key|apikey)", re.IGNORECASE)
_SECRET_TEXT = (
    re.compile(r"dapi[0-9a-f]{32}(?:-\d+)?"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)((?:password|passwd|secret|token|api[_-]?key)\s*[=:]\s*)\S+"),
)


def redact_text(text: str) -> str:
    """Return ``text`` with Databricks tokens, bearer tokens and ``password=...``-style values masked."""
    for pattern in _SECRET_TEXT:
        text = pattern.sub(lambda match: (match.group(1) if match.lastindex else "") + REDACTED, text)
    return text


def redact_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    """Return ``values`` with every secret-looking key's value masked and string values scrubbed."""
    clean: dict[str, Any] = {}
    for key, value in values.items():
        if _SECRET_KEY.search(str(key)):
            clean[key] = REDACTED
        elif isinstance(value, str):
            clean[key] = redact_text(value)
        else:
            clean[key] = value
    return clean


def _patch(record: dict) -> None:
    record["message"] = redact_text(record["message"])
    record["extra"] = redact_mapping(record["extra"])


class RunEventBuffer:
    """A loguru sink that keeps the run's events, numbered in order, for ``md_run_event``."""

    def __init__(self, run_id: str, max_events: int = 1000):
        self.run_id = run_id
        self.max_events = max_events
        self.events: list[RunEventRecord] = []
        self.dropped = 0

    def write(self, message: Any) -> None:
        """Keep one loguru message (its ``record``); past ``max_events`` events are counted, not kept."""
        if len(self.events) >= self.max_events:
            self.dropped += 1
            return
        record = message.record
        # loguru stamps records with its own datetime subclass, which Spark refuses; store a plain UTC datetime
        context = {key: value for key, value in record["extra"].items() if key != "run_id"}
        self.events.append(
            RunEventRecord(
                run_id=self.run_id,
                seq=len(self.events),
                level=record["level"].name,
                message=record["message"],
                logged_at=datetime.fromtimestamp(record["time"].timestamp(), tz=timezone.utc),
                context={**context, "function": record["function"], "module": record["module"]},
            )
        )


def configure_run_logging(
    context: Mapping[str, Any],
    *,
    level: str = "INFO",
    stream: TextIO | None = None,
    buffer: RunEventBuffer | None = None,
) -> RunEventBuffer:
    """
    Send loguru output to ``stream`` (stdout by default) as redacted JSON and into a run-event buffer.

    ``context`` is bound to every record (it must include ``run_id``). Returns the buffer, to be written to
    ``md_run_event`` when the task ends. Calling it again replaces the earlier configuration.
    """
    run_id = str(context["run_id"])
    events = buffer or RunEventBuffer(run_id)
    logger.remove()
    logger.configure(extra={key: str(value) for key, value in context.items()}, patcher=_patch)
    logger.add(stream or sys.stdout, level=level, serialize=True)
    logger.add(events.write, level=level)
    return events


__all__ = ["RunEventBuffer", "configure_run_logging", "logger", "redact_mapping", "redact_text"]
