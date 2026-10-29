"""Tests for loguru run logging: JSON with run context, secret masking, and the run-event buffer."""

import io
import json
from datetime import (
    datetime,
    timezone,
)

from dbxops.runlog import (
    REDACTED,
    RunEventBuffer,
    configure_run_logging,
    logger,
    redact_mapping,
    redact_text,
)


def test__tokens_and_secret_values_are_masked():
    """Databricks and bearer tokens, password=... values and secret-looking keys never reach a sink."""
    text = "token dapi" + "0" * 32 + "-2 and Bearer abcdefghijklmnop and password=hunter2"
    assert redact_text(text) == f"token {REDACTED} and Bearer {REDACTED} and password={REDACTED}"
    assert redact_mapping({"client_secret": "x", "note": "password: pw", "rows": 3}) == {
        "client_secret": REDACTED,
        "note": f"password: {REDACTED}",
        "rows": 3,
    }


def test__every_line_is_json_with_the_run_context_and_is_kept_for_md_run_event():
    """The stream gets one JSON object per line with the bound context; the buffer numbers events in order."""
    stream = io.StringIO()
    events = configure_run_logging({"run_id": "42", "target": "dev", "repo_id": "cf"}, stream=stream)
    logger.info("loaded {} rows", 7, table="silver.fact_claim")
    logger.warning("api_key=sk-123456789012345")
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert lines[0]["record"]["extra"] == {
        "run_id": "42",
        "target": "dev",
        "repo_id": "cf",
        "table": "silver.fact_claim",
    }
    assert lines[0]["record"]["message"] == "loaded 7 rows"
    assert [(e.seq, e.level) for e in events.events] == [(0, "INFO"), (1, "WARNING")]
    assert events.events[0].logged_at.__class__ is datetime and events.events[0].logged_at.tzinfo is timezone.utc
    assert events.events[0].context["table"] == "silver.fact_claim" and "run_id" not in events.events[0].context
    assert "sk-123" not in stream.getvalue() and events.events[1].message.endswith(REDACTED)


def test__the_buffer_stops_keeping_events_at_its_limit():
    """Past max_events, events are counted as dropped so a chatty run cannot grow the table without bound."""
    events = configure_run_logging({"run_id": "1"}, stream=io.StringIO(), buffer=RunEventBuffer("1", max_events=2))
    for n in range(5):
        logger.info("event {}", n)
    assert len(events.events) == 2 and events.dropped == 3
