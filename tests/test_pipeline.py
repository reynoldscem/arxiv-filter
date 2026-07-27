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
