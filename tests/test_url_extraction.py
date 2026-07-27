import tempfile
import os

import models

# Point the app at a throwaway DB before import triggers init_db().
models.DB_PATH = os.path.join(tempfile.mkdtemp(), "test.db")

from arxiv_app import extract_code_urls_filter


def test_code_url_from_abstract():
    paper = {"abstract": "Code at https://github.com/foo/bar.", "comment": None}
    assert extract_code_urls_filter(paper) == [("code", "https://github.com/foo/bar")]


def test_trailing_punctuation_stripped():
    paper = {"abstract": "See https://github.com/foo/bar), thanks.", "comment": None}
    assert extract_code_urls_filter(paper) == [("code", "https://github.com/foo/bar")]


def test_project_url_needs_project_keyword():
    with_kw = {"abstract": None, "comment": "Project page: https://foo.github.io/bar"}
    without_kw = {"abstract": None, "comment": "See https://foo.github.io/bar"}
    assert ("project", "https://foo.github.io/bar") in extract_code_urls_filter(with_kw)
    assert extract_code_urls_filter(without_kw) == []


def test_arxiv_urls_excluded_from_project_links():
    paper = {"abstract": None, "comment": "Project page https://arxiv.org/abs/1234.5678"}
    assert extract_code_urls_filter(paper) == []


def test_db_fields_take_priority_and_dedupe():
    paper = {
        "code_url": "https://github.com/foo/bar",
        "project_url": "https://foo.github.io/",
        "abstract": "Code: https://github.com/foo/bar",
        "comment": None,
    }
    urls = extract_code_urls_filter(paper)
    assert urls == [
        ("project", "https://foo.github.io/"),
        ("code", "https://github.com/foo/bar"),
    ]
