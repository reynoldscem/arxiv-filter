import json
import subprocess

import pytest

import cron_update
from cron_update import split_batches


def test_stage2_empty_candidates_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(cron_update, "DEBUG_DIR", str(tmp_path))
    papers = [{"arxiv_id": "2601.00001", "title": "T", "abstract": "A"}]
    assert cron_update.stage2_filter(papers, [], "criteria", save_tag="test") == []


@pytest.mark.parametrize("n_items,max_size", [(0, 200), (1, 200), (200, 200), (201, 200), (450, 200), (614, 200), (29, 12)])
def test_split_batches(n_items, max_size):
    items = list(range(n_items))
    batches = split_batches(items, max_size)
    assert sorted(x for b in batches for x in b) == items
    assert all(len(b) <= max_size for b in batches)
    if batches:
        assert max(len(b) for b in batches) - min(len(b) for b in batches) <= 1


@pytest.mark.parametrize("announcement,today,last_run,expected", [
    # Yesterday's announcement still in the feed the next morning
    ("2026-10-06", "2026-10-07", {"announcement_date": "2026-10-06", "ingest_date": "2026-10-06"}, True),
    # Same-day re-run picks up late papers
    ("2026-10-07", "2026-10-07", {"announcement_date": "2026-10-07", "ingest_date": "2026-10-07"}, False),
    # Feed has moved on
    ("2026-10-07", "2026-10-07", {"announcement_date": "2026-10-06", "ingest_date": "2026-10-06"}, False),
    # No history
    ("2026-10-06", "2026-10-07", {}, False),
])
def test_is_stale_feed(announcement, today, last_run, expected):
    assert cron_update.is_stale_feed(announcement, today, last_run) is expected


def test_last_run_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(cron_update, "STATE_FILE", str(tmp_path / "last_run.json"))
    assert cron_update.load_last_run() == {}
    cron_update.save_last_run("2026-10-07", "2026-10-07")
    assert cron_update.load_last_run() == {"announcement_date": "2026-10-07", "ingest_date": "2026-10-07", "paper_ids": []}


def test_claude_error_envelope_raises(monkeypatch):
    # Usage limits / API errors exit 0 with the message in "result" and no structured_output
    envelope = {"type": "result", "is_error": True, "subtype": "success", "result": "Usage limit reached"}
    monkeypatch.setattr(cron_update.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(envelope), ""))
    with pytest.raises(cron_update.ClaudeFilterError):
        cron_update.claude_filter.__wrapped__("prompt", json_schema=cron_update.STAGE1_SCHEMA)


def test_claude_structured_output_returned(monkeypatch):
    envelope = {"type": "result", "is_error": False, "result": "{}", "structured_output": {"candidate_ids": ["2610.00001"]}}
    monkeypatch.setattr(cron_update.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(envelope), ""))
    assert cron_update.claude_filter.__wrapped__("prompt", json_schema=cron_update.STAGE1_SCHEMA) == {"candidate_ids": ["2610.00001"]}


def test_stage1_failed_batch_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(cron_update, "DEBUG_DIR", str(tmp_path))
    monkeypatch.setattr(cron_update, "failed_batches", [])

    def fake_batch(batch, batch_idx, month_prefix, criteria):
        if batch_idx == 1:
            raise cron_update.ClaudeFilterError("usage limit")
        return [batch[0]["arxiv_id"]]

    monkeypatch.setattr(cron_update, "_stage1_batch", fake_batch)
    papers = [{"arxiv_id": f"2610.{i:05d}", "title": "T"} for i in range(cron_update.STAGE1_BATCH_SIZE + 1)]
    candidates = cron_update.stage1_candidates(papers, "2026-10-26", "criteria")
    assert len(candidates) == 1
    assert cron_update.failed_batches == ["stage1[1]"]


@pytest.mark.parametrize("now,expected", [
    ("2026-11-03 05:00", "2026-11-03 05:15"),
    ("2026-11-03 10:45", "2026-11-03 11:00"),  # landing exactly on the deadline is allowed
    ("2026-11-03 10:50", None),
    ("2026-11-03 12:00", None),  # midday top-up runs don't poll
])
def test_next_poll_time(now, expected):
    from datetime import datetime
    fmt = "%Y-%m-%d %H:%M"
    got = cron_update.next_poll_time(datetime.strptime(now, fmt))
    assert got == (datetime.strptime(expected, fmt) if expected else None)


def test_lock_blocks_second_run(tmp_path, monkeypatch):
    monkeypatch.setattr(cron_update, "LOCK_FILE", str(tmp_path / "cron.lock"))
    first = cron_update.acquire_lock()
    assert first is not None
    assert cron_update.acquire_lock() is None
    first.close()
    second = cron_update.acquire_lock()
    assert second is not None
    second.close()


@pytest.mark.parametrize("ids,today,last_run,expected", [
    # Feed re-dated overnight but still holds yesterday's papers
    (["a", "b"], "2026-11-10", {"ingest_date": "2026-11-09", "paper_ids": ["a", "b", "c"]}, True),
    # At least one new paper: the feed has moved on
    (["a", "d"], "2026-11-10", {"ingest_date": "2026-11-09", "paper_ids": ["a", "b", "c"]}, False),
    # Same-day top-up run: always proceed
    (["a", "b"], "2026-11-10", {"ingest_date": "2026-11-10", "paper_ids": ["a", "b"]}, False),
    # Old state file without paper_ids
    (["a"], "2026-11-10", {"ingest_date": "2026-11-09"}, False),
    ([], "2026-11-10", {"ingest_date": "2026-11-09", "paper_ids": ["a"]}, False),
])
def test_repeats_last_run(ids, today, last_run, expected):
    papers = [{"arxiv_id": i} for i in ids]
    assert cron_update.repeats_last_run(papers, today, last_run) is expected


def test_last_run_saves_paper_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(cron_update, "STATE_FILE", str(tmp_path / "last_run.json"))
    cron_update.save_last_run("2026-11-10", "2026-11-10", ["b", "a"])
    assert cron_update.load_last_run()["paper_ids"] == ["a", "b"]
