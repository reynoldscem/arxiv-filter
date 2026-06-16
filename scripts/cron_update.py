#!/usr/bin/env python3
"""Daily arXiv paper fetch + Claude filtering + app ingestion.

Usage:
    python3 cron_update.py [YYYY-MM-DD]
    If no date given, uses the arXiv RSS announcement date.
"""

import json
import math
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

import backoff

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(ROOT_DIR, "app", "data")
APP_PORT = 5713
CLAUDE = "/home/charlie/.local/bin/claude"
CLAUDE_MODEL = "claude-sonnet-4-6"
ARXIV_RSS_URL = "https://rss.arxiv.org/rss/cs.CV"


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def log_separator(title):
    log(f"{'=' * 20} {title} {'=' * 20}")


def fetch_papers(target_date, force=False):
    """Fetch papers. Skip fetch if file exists unless force=True."""
    papers_file = os.path.join(ROOT_DIR, f"cs_cv_{target_date}.json")
    if not force and os.path.exists(papers_file) and os.path.getsize(papers_file) > 10:
        size = os.path.getsize(papers_file)
        log(f"Using existing {papers_file} ({size:,} bytes)")
    else:
        if force and os.path.exists(papers_file):
            log(f"Re-fetching (force=True, overwriting existing file)")
        log("Fetching papers from arXiv...")
        result = subprocess.run(
            ["python3", "fetch_arxiv_cv.py", "-o", "json", "-s", papers_file],
            cwd=SCRIPT_DIR, capture_output=True, text=True,
        )
        log(f"fetch stdout: {result.stdout.strip()}")
        if result.stderr.strip():
            log(f"fetch stderr: {result.stderr.strip()}")
        if result.returncode != 0:
            raise RuntimeError(f"fetch_arxiv_cv.py failed (exit {result.returncode})")

    with open(papers_file) as f:
        papers = json.load(f)

    if not papers:
        log(f"No papers for {target_date} (normal on weekends/holidays)")
        return []

    return papers


class ClaudeFilterError(RuntimeError):
    """Raised when claude filtering fails and should be retried."""
    pass


def _on_backoff(details):
    log(f"claude: retry #{details['tries']} after {details['wait']:.1f}s — {details['exception']}")


@backoff.on_exception(
    backoff.expo,
    (ClaudeFilterError, subprocess.TimeoutExpired),
    max_tries=3,
    on_backoff=_on_backoff,
)
def claude_filter(prompt, label="claude", json_schema=None, timeout=600):
    """Run claude --print with structured JSON output."""
    log(f"{label}: sending {len(prompt):,} chars to claude (timeout={timeout}s)...")

    cmd = [CLAUDE, "--print", "-p", prompt, "--output-format", "json"]
    if CLAUDE_MODEL:
        cmd += ["--model", CLAUDE_MODEL]
    if json_schema:
        cmd += ["--json-schema", json.dumps(json_schema)]

    result = subprocess.run(
        cmd,
        capture_output=True, text=True, cwd=ROOT_DIR,
        timeout=timeout,
    )

    log(f"{label}: exit code {result.returncode}, stdout {len(result.stdout):,} chars, stderr {len(result.stderr):,} chars")

    if result.stderr.strip():
        log(f"{label} stderr: {result.stderr[:1000]}")

    if result.returncode != 0:
        log(f"{label} stdout (first 500): {result.stdout[:500]}")
        raise ClaudeFilterError(f"{label} failed (exit {result.returncode})")

    if not result.stdout.strip():
        log(f"{label}: WARNING — empty output (raw bytes: {repr(result.stdout[:50])})")
        raise ClaudeFilterError(f"{label} returned empty output")

    # Parse the JSON envelope from --output-format json
    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        log(f"{label}: failed to parse JSON envelope: {e}")
        log(f"{label}: raw output (first 500): {result.stdout[:500]}")
        raise ClaudeFilterError(f"{label} returned invalid JSON envelope")

    # Extract the actual result
    if json_schema and "structured_output" in envelope:
        return envelope["structured_output"]
    elif "result" in envelope:
        return envelope["result"]
    else:
        log(f"{label}: unexpected envelope keys: {list(envelope.keys())}")
        raise ClaudeFilterError(f"{label} missing result in JSON envelope")


# JSON schemas for structured output

STAGE1_SCHEMA = {
    "type": "object",
    "properties": {
        "candidate_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "arXiv IDs of candidate papers worth reading abstracts for",
        }
    },
    "required": ["candidate_ids"],
}

STAGE2_PAPER_SCHEMA = {
    "type": "object",
    "properties": {
        "papers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "arxiv_id": {"type": "string"},
                    "title": {"type": "string"},
                    "authors": {"type": "array", "items": {"type": "string"}},
                    "abstract": {"type": "string"},
                    "categories": {"type": "array", "items": {"type": "string"}},
                    "published": {"type": "string"},
                    "pdf_url": {"type": "string"},
                    "abs_url": {"type": "string"},
                    "comment": {"type": "string"},
                    "relevance_tier": {"type": "string", "enum": ["high", "moderate"]},
                    "relevance_summary": {"type": "string"},
                },
                "required": ["arxiv_id", "title", "authors", "abstract", "published",
                             "pdf_url", "abs_url", "relevance_tier", "relevance_summary"],
            },
        }
    },
    "required": ["papers"],
}


STAGE1_BATCH_SIZE = 200


def _stage1_batch(batch, batch_idx, month_prefix, criteria):
    """Scan titles for a single batch. Runs in a thread."""
    label = f"stage1[{batch_idx}]"
    title_lines = "\n".join(
        f"{p['arxiv_id']}  {p['title']}  (published: {p.get('published', '?')})"
        for p in batch
    )
    prompt = (
        f"Here are the filter criteria:\n\n{criteria}\n\n"
        f"--- PAPERS ---\n{title_lines}\n\n"
        f"Return candidate arxiv_id strings for papers worth reading the abstract of. "
        f"Exclude revisions (ID prefix must be {month_prefix}). "
        f"Be generous — include anything that could plausibly be relevant. "
        f"Only exclude papers clearly outside the criteria. "
        f"A second stage will read abstracts and make the final call."
    )
    result = claude_filter(prompt, label=label, json_schema=STAGE1_SCHEMA)
    return result["candidate_ids"]


def stage1_candidates(papers, target_date, criteria):
    """Stage 1: scan titles, return candidate IDs (batched + parallel)."""
    month_prefix = target_date.replace("-", "")[2:6]  # e.g. "2603"

    n_batches = max(1, math.ceil(len(papers) / STAGE1_BATCH_SIZE))
    batch_size = math.ceil(len(papers) / n_batches)
    batches = [papers[i:i + batch_size] for i in range(0, len(papers), batch_size)]
    log(f"stage1: scanning {len(papers)} papers in {len(batches)} batch(es) of ~{batch_size}...")

    all_candidates = []
    if len(batches) == 1:
        all_candidates = _stage1_batch(batches[0], 0, month_prefix, criteria)
    else:
        with ThreadPoolExecutor(max_workers=len(batches)) as pool:
            futures = {
                pool.submit(_stage1_batch, batch, i, month_prefix, criteria): i
                for i, batch in enumerate(batches)
            }
            for future in as_completed(futures):
                batch_idx = futures[future]
                try:
                    batch_ids = future.result()
                    log(f"stage1[{batch_idx}]: returned {len(batch_ids)} candidates")
                    all_candidates.extend(batch_ids)
                except Exception as e:
                    log(f"stage1[{batch_idx}]: FAILED — {e}")
                    log(traceback.format_exc())

    # Save for debugging
    candidates_file = os.path.join(DATA_DIR, f"candidates_{target_date}.json")
    with open(candidates_file, "w") as f:
        json.dump({"candidate_ids": all_candidates}, f, indent=2)
    log(f"stage1: raw output saved to {candidates_file}")

    log(f"stage1: {len(all_candidates)} candidates: {all_candidates}")
    return all_candidates


STAGE2_BATCH_SIZE = 12  # Max candidates per batch


def _stage2_batch(batch, batch_idx, criteria):
    """Filter a single batch of candidates. Runs in a thread."""
    label = f"stage2[{batch_idx}]"
    prompt = (
        f"Here are the filter criteria:\n\n{criteria}\n\n"
        f"--- CANDIDATE PAPERS (full details) ---\n{json.dumps(batch, indent=2)}\n\n"
        f"Select the relevant papers. For each, include all fields from the input "
        f"plus relevance_tier (high/moderate) and relevance_summary (one line). "
        f"Exclude revisions."
    )
    result = claude_filter(prompt, label=label, json_schema=STAGE2_PAPER_SCHEMA, timeout=600)
    return result["papers"]


def stage2_filter(papers, candidate_ids, criteria, save_tag=None):
    """Stage 2: read candidate abstracts, produce final filtered list (batched)."""
    selected = [p for p in papers if p["arxiv_id"] in candidate_ids]
    missing = set(candidate_ids) - {p["arxiv_id"] for p in selected}
    if missing:
        log(f"stage2: WARNING — {len(missing)} candidate IDs not found in papers: {missing}")

    n_batches = max(1, math.ceil(len(selected) / STAGE2_BATCH_SIZE))
    batch_size = math.ceil(len(selected) / n_batches)
    batches = [selected[i:i + batch_size] for i in range(0, len(selected), batch_size)]
    log(f"stage2: filtering {len(selected)} candidates in {len(batches)} batch(es) of ~{batch_size}...")

    all_papers = []
    if len(batches) == 1:
        all_papers = _stage2_batch(batches[0], 0, criteria)
    else:
        with ThreadPoolExecutor(max_workers=len(batches)) as pool:
            futures = {
                pool.submit(_stage2_batch, batch, i, criteria): i
                for i, batch in enumerate(batches)
            }
            for future in as_completed(futures):
                batch_idx = futures[future]
                try:
                    batch_papers = future.result()
                    log(f"stage2[{batch_idx}]: returned {len(batch_papers)} papers")
                    all_papers.extend(batch_papers)
                except Exception as e:
                    log(f"stage2[{batch_idx}]: FAILED — {e}")
                    log(traceback.format_exc())

    # Save for debugging
    tag = save_tag or target_date
    raw_file = os.path.join(DATA_DIR, f"stage2_raw_{tag}.json")
    with open(raw_file, "w") as f:
        json.dump({"papers": all_papers}, f, indent=2)
    log(f"stage2: raw output saved to {raw_file}")

    return all_papers


def ingest(target_date, filtered_json):
    """POST filtered papers to the app."""
    url = f"http://localhost:{APP_PORT}/api/papers?date={target_date}"
    log(f"Ingesting to {url} ({len(filtered_json):,} bytes)...")
    req = urllib.request.Request(
        url,
        data=filtered_json.encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.load(resp)
        log(f"Ingestion response: {result}")
    except urllib.error.URLError as e:
        log(f"Ingestion FAILED — is the app running? {e}")
        raise


def check_app_running():
    """Verify the Flask app is reachable before starting."""
    try:
        with urllib.request.urlopen(f"http://localhost:{APP_PORT}/", timeout=5):
            return True
    except Exception as e:
        log(f"WARNING: app not reachable at localhost:{APP_PORT}: {e}")
        return False


def get_arxiv_announcement_date():
    """Get the announcement date from the arXiv RSS feed.

    arXiv announces papers Sun-Thu at 20:00 ET. The RSS pubDate reflects
    the announcement date (e.g. "Mon, 9 Mar 2026"), which is what arXiv
    shows on its /list/cs.CV/recent page.
    """
    try:
        with urllib.request.urlopen(ARXIV_RSS_URL, timeout=15) as resp:
            xml_data = resp.read().decode("utf-8")
        root = ET.fromstring(xml_data)
        # pubDate is on the channel, e.g. "Tue, 10 Mar 2026 00:00:00 -0400"
        pub_date = root.find(".//channel/pubDate")
        if pub_date is not None and pub_date.text:
            # Parse "Tue, 10 Mar 2026 00:00:00 -0400" → "2026-03-10"
            match = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", pub_date.text)
            if match:
                dt = datetime.strptime(
                    f"{match.group(1)} {match.group(2)} {match.group(3)}", "%d %b %Y"
                )
                return dt.strftime("%Y-%m-%d")
    except Exception as e:
        log(f"WARNING: could not get RSS pubDate: {e}")
    return None


def main():
    global target_date, CLAUDE_MODEL

    # Parse --model flag if present
    args = sys.argv[1:]
    if "--model" in args:
        idx = args.index("--model")
        CLAUDE_MODEL = args[idx + 1]
        args = args[:idx] + args[idx + 2:]
        log(f"Using Claude model: {CLAUDE_MODEL}")

    explicit_date = len(args) > 0
    if explicit_date:
        # Backfill mode: fetch and ingest under the given date
        target_date = args[0]
        ingest_date = target_date
    else:
        # Cron mode: fetch by arXiv's announcement date, but ingest under today's
        # date so "today in the app" = "what we fetched today"
        target_date = get_arxiv_announcement_date() or date.today().isoformat()
        ingest_date = date.today().isoformat()

    os.makedirs(DATA_DIR, exist_ok=True)

    log_separator(f"arXiv update for {target_date} (ingesting as {ingest_date})")

    # Pre-flight checks
    if not check_app_running():
        log("App not running — will still fetch and filter, but ingestion will fail")

    # Load filter criteria
    criteria_path = os.path.join(ROOT_DIR, "filter_criteria.md")
    with open(criteria_path) as f:
        criteria = f.read()
    log(f"Filter criteria loaded ({len(criteria):,} chars)")

    # 1. Fetch — always re-fetch in automatic mode (cron) since the RSS
    # may have updated since the last run with the same date
    log_separator("Stage 0: Fetch")
    papers = fetch_papers(target_date, force=not explicit_date)
    log(f"Total papers fetched: {len(papers)}")

    # arXiv publishes Sun-Thu, so weekday fetches (Mon-Fri) should have papers.
    # If we get 0 on a weekday, the feed is probably stale — retry up to 3 times.
    # Check the *current* day, not the RSS date — on Monday morning the RSS may
    # still show Sunday's (empty) date if the feed hasn't updated yet.
    FETCH_RETRY_DELAY = 30 * 60  # 30 minutes
    FETCH_MAX_RETRIES = 3
    if not papers and not explicit_date:
        today_weekday = datetime.now().weekday()  # 0=Mon
        is_weekday = today_weekday < 5  # Sat=5, Sun=6
        if is_weekday:
            for attempt in range(1, FETCH_MAX_RETRIES + 1):
                log(f"0 papers on a weekday — retry {attempt}/{FETCH_MAX_RETRIES} in {FETCH_RETRY_DELAY // 60} min...")
                # Delete the empty file so fetch_papers re-fetches
                empty_file = os.path.join(ROOT_DIR, f"cs_cv_{target_date}.json")
                if os.path.exists(empty_file):
                    os.remove(empty_file)
                time.sleep(FETCH_RETRY_DELAY)
                # Re-check RSS date in case it updated during the wait
                target_date = get_arxiv_announcement_date() or date.today().isoformat()
                papers = fetch_papers(target_date, force=True)
                log(f"Retry {attempt}: fetched {len(papers)} papers (date: {target_date})")
                if papers:
                    break

    if not papers:
        log_separator("Complete (no papers)")
        return

    # 2. Stage 1: title scan
    log_separator("Stage 1: Title scan")
    candidate_ids = stage1_candidates(papers, target_date, criteria)
    log(f"Candidates after stage 1: {len(candidate_ids)}")

    # 3. Stage 2: abstract filter
    log_separator("Stage 2: Abstract filter")
    filtered = stage2_filter(papers, candidate_ids, criteria)

    filtered_json = json.dumps(filtered, indent=2)
    filtered_file = os.path.join(DATA_DIR, f"filtered_{target_date}.json")
    with open(filtered_file, "w") as f:
        f.write(filtered_json)

    log(f"Final papers: {len(filtered)}")
    for p in filtered:
        tier = p.get("relevance_tier", "?")
        log(f"  [{tier.upper()}] {p.get('arxiv_id', '?')} — {p.get('title', '?')}")

    # 4. Ingest
    log_separator("Stage 3: Ingest")
    ingest(ingest_date, filtered_json)

    # 5. Fetch thumbnails for papers with project pages
    log_separator("Stage 4: Thumbnails")
    try:
        sys.path.insert(0, os.path.join(ROOT_DIR, "app"))
        from fetch_thumbnails import fetch_all_missing
        count = fetch_all_missing()
        log(f"Thumbnails fetched: {count}")
    except Exception as e:
        log(f"Thumbnail fetch failed (non-fatal): {e}")
        log(traceback.format_exc())

    # Coverage summary for today's batch
    log_separator("Coverage")
    try:
        from models import get_db
        conn = get_db()
        total = conn.execute("SELECT COUNT(*) FROM papers WHERE date_added = ?", (ingest_date,)).fetchone()[0]
        thumbs = conn.execute("SELECT COUNT(*) FROM papers WHERE date_added = ? AND thumbnail IS NOT NULL", (target_date,)).fetchone()[0]
        code = conn.execute("SELECT COUNT(*) FROM papers WHERE date_added = ? AND code_url IS NOT NULL", (target_date,)).fetchone()[0]
        no_thumb = conn.execute(
            "SELECT arxiv_id, title FROM papers WHERE date_added = ? AND thumbnail IS NULL", (ingest_date,)
        ).fetchall()
        conn.close()
        log(f"Thumbnails: {thumbs}/{total} ({100*thumbs//total if total else 0}%)")
        log(f"Code URLs:  {code}/{total} ({100*code//total if total else 0}%)")
        for row in no_thumb:
            log(f"  missing thumb: {row['arxiv_id']} — {row['title']}")
    except Exception as e:
        log(f"Coverage check failed: {e}")

    log_separator("Complete")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FATAL ERROR: {e}")
        log(traceback.format_exc())
        sys.exit(1)
