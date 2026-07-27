import pytest

import models


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "DB_PATH", str(tmp_path / "test.db"))
    models.init_db()


def paper(arxiv_id="2601.00001", **overrides):
    p = {
        "arxiv_id": arxiv_id,
        "title": "Test Paper",
        "authors": ["A. Author", "B. Author"],
        "abstract": "An abstract.",
        "published": "2026-01-01T00:00:00Z",
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
        "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
        "relevance_tier": "high",
        "relevance_summary": "Relevant.",
    }
    p.update(overrides)
    return p


def test_insert_counts_new_and_updated(db):
    new, updated = models.insert_papers([paper(), paper("2601.00002")], "2026-01-02")
    assert (new, updated) == (2, 0)

    new, updated = models.insert_papers([paper(), paper("2601.00003")], "2026-01-02")
    assert (new, updated) == (1, 1)


def test_reingest_preserves_date_added(db):
    models.insert_papers([paper()], "2026-01-02")
    models.insert_papers([paper()], "2026-01-03")

    assert models.get_papers_by_date("2026-01-03") == []
    papers = models.get_papers_by_date("2026-01-02")
    assert len(papers) == 1


def test_upsert_updates_metadata(db):
    models.insert_papers([paper()], "2026-01-02")
    models.insert_papers([paper(title="New Title", relevance_tier="moderate")], "2026-01-02")

    (p,) = models.get_papers_by_date("2026-01-02")
    assert p["title"] == "New Title"
    assert p["relevance_tier"] == "moderate"


def test_tier_ordering(db):
    models.insert_papers([
        paper("2601.00001", relevance_tier="moderate"),
        paper("2601.00002", relevance_tier="high"),
    ], "2026-01-02")

    tiers = [p["relevance_tier"] for p in models.get_papers_by_date("2026-01-02")]
    assert tiers == ["high", "moderate"]


def test_authors_round_trip_as_list(db):
    models.insert_papers([paper()], "2026-01-02")
    (p,) = models.get_papers_by_date("2026-01-02")
    assert p["authors"] == ["A. Author", "B. Author"]


def test_toggle_favourite(db):
    models.insert_papers([paper()], "2026-01-02")
    assert models.toggle_favourite("2601.00001") == 1
    assert [p["arxiv_id"] for p in models.get_favourites()] == ["2601.00001"]
    assert models.toggle_favourite("2601.00001") == 0
    assert models.get_favourites() == []


def test_toggle_missing_paper_returns_none(db):
    assert models.toggle_favourite("9999.99999") is None
    assert models.toggle_dismissed("9999.99999") is None
