"""Flask app for browsing filtered arXiv papers."""

import re
from datetime import date, datetime

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

from models import (
    get_available_dates,
    get_favourites,
    get_papers_by_date,
    init_db,
    insert_papers,
    toggle_dismissed,
    toggle_favourite,
    update_favourite_note,
)

app = Flask(__name__)

init_db()

_CODE_URL_RE = re.compile(r'https?://(?:github\.com|gitlab\.com|bitbucket\.org|huggingface\.co)/\S+')
_PROJECT_URL_RE = re.compile(r'https?://\S+')


@app.template_filter("extract_code_urls")
def extract_code_urls_filter(paper):
    """Extract code/project URLs from DB fields, abstract, and comment."""
    urls = []
    seen = set()

    # DB-stored project URL (scraped from project page / GitHub README)
    if paper.get("project_url"):
        urls.append(("project", paper["project_url"]))
        seen.add(paper["project_url"])

    # DB-stored code URL (scraped from project page)
    if paper.get("code_url"):
        urls.append(("code", paper["code_url"]))
        seen.add(paper["code_url"])

    for text in (paper.get("abstract", ""), paper.get("comment", "")):
        if not text:
            continue
        # Code hosting URLs
        for u in _CODE_URL_RE.findall(text):
            clean = u.rstrip(".,;:)")
            if clean not in seen:
                urls.append(("code", clean))
                seen.add(clean)
        # Project page URLs (from comment field typically)
        if "project" in text.lower():
            for u in _PROJECT_URL_RE.findall(text):
                clean = u.rstrip(".,;:)")
                if clean not in seen and "arxiv.org" not in clean:
                    urls.append(("project", clean))
                    seen.add(clean)
    return urls


def _date_nav(current_date, dates):
    """Compute prev/next available dates for navigation."""
    date_list = [d["date_added"] for d in dates]
    prev_date = next_date = None
    if current_date in date_list:
        idx = date_list.index(current_date)
        if idx < len(date_list) - 1:
            prev_date = date_list[idx + 1]  # dates are newest-first
        if idx > 0:
            next_date = date_list[idx - 1]
    return prev_date, next_date


@app.route("/")
def index():
    """Show papers for today (or most recent available date)."""
    dates = get_available_dates()
    if dates:
        latest = dates[0]["date_added"]
        papers = get_papers_by_date(latest)
        current_date = latest
    else:
        papers = []
        current_date = date.today().isoformat()
    prev_date, next_date = _date_nav(current_date, dates)
    return render_template(
        "index.html", papers=papers, current_date=current_date, dates=dates,
        prev_date=prev_date, next_date=next_date,
    )


@app.route("/date/<date_str>")
def papers_by_date(date_str):
    """Show papers for a specific date."""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        abort(400, "Invalid date format. Use YYYY-MM-DD.")
    dates = get_available_dates()
    papers = get_papers_by_date(date_str)
    prev_date, next_date = _date_nav(date_str, dates)
    return render_template(
        "index.html", papers=papers, current_date=date_str, dates=dates,
        prev_date=prev_date, next_date=next_date,
    )


@app.route("/favourites")
def favourites():
    """Show all favourited papers."""
    papers = get_favourites()
    return render_template("favourites.html", papers=papers)


# --- API endpoints ---


@app.route("/api/papers", methods=["POST"])
def api_ingest_papers():
    """Ingest filtered papers. Accepts JSON array.

    Optional query param: ?date=YYYY-MM-DD (defaults to today).
    """
    data = request.get_json(force=True)
    if not isinstance(data, list):
        return jsonify({"error": "Expected a JSON array"}), 400

    date_added = request.args.get("date", date.today().isoformat())
    count = insert_papers(data, date_added)
    return jsonify({"inserted": count, "date": date_added})


@app.route("/api/favourite/<arxiv_id>", methods=["POST"])
def api_toggle_favourite(arxiv_id):
    """Toggle favourite status for a paper."""
    result = toggle_favourite(arxiv_id)
    if result is None:
        return jsonify({"error": "Paper not found"}), 404
    return jsonify({"arxiv_id": arxiv_id, "is_favourite": result})


@app.route("/api/favourite/<arxiv_id>/note", methods=["POST"])
def api_update_note(arxiv_id):
    """Add or edit a note on a favourite."""
    data = request.get_json(force=True)
    note = data.get("note", "")
    update_favourite_note(arxiv_id, note)
    return jsonify({"arxiv_id": arxiv_id, "note": note})


@app.route("/api/dismiss/<arxiv_id>", methods=["POST"])
def api_toggle_dismissed(arxiv_id):
    """Toggle dismissed status for a paper."""
    result = toggle_dismissed(arxiv_id)
    if result is None:
        return jsonify({"error": "Paper not found"}), 404
    return jsonify({"arxiv_id": arxiv_id, "is_dismissed": result})


if __name__ == "__main__":
    import os

    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=5713, debug=debug)
