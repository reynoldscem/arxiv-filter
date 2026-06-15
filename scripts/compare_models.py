#!/usr/bin/env python3
"""Run stage 1 + stage 2 filtering with multiple models and compare results."""

import json
import os
import sys
import shutil

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(ROOT_DIR, "app", "data")
sys.path.insert(0, SCRIPT_DIR)

from cron_update import (
    fetch_papers, stage1_candidates, stage2_filter, log, log_separator,
    get_arxiv_announcement_date,
)
import cron_update

MODELS = ["claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-6"]
TARGET_DATE = "2026-06-15"


def run_model(model_name, papers, criteria):
    """Run both filter stages with a specific model."""
    cron_update.CLAUDE_MODEL = model_name
    short = model_name.split("-")[1]  # haiku, sonnet, opus
    log_separator(f"{short} — Stage 1")

    candidate_ids = stage1_candidates(papers, TARGET_DATE, criteria)
    log(f"{short} stage1: {len(candidate_ids)} candidates")

    log_separator(f"{short} — Stage 2")
    filtered = stage2_filter(papers, candidate_ids, criteria, save_tag=f"{short}_{TARGET_DATE}")
    log(f"{short} stage2: {len(filtered)} papers")

    out_file = os.path.join(DATA_DIR, f"compare_{short}_{TARGET_DATE}.json")
    with open(out_file, "w") as f:
        json.dump(filtered, f, indent=2)
    log(f"Saved to {out_file}")

    return {
        "model": short,
        "candidates": len(candidate_ids),
        "candidate_ids": candidate_ids,
        "final": len(filtered),
        "papers": filtered,
    }


def main():
    criteria_path = os.path.join(ROOT_DIR, "filter_criteria.md")
    with open(criteria_path) as f:
        criteria = f.read()

    papers = fetch_papers(TARGET_DATE, force=False)
    log(f"Loaded {len(papers)} papers for {TARGET_DATE}")

    results = {}
    for model in MODELS:
        short = model.split("-")[1]
        try:
            results[short] = run_model(model, papers, criteria)
        except Exception as e:
            log(f"{short}: FAILED — {e}")
            import traceback
            log(traceback.format_exc())

    # Summary
    log_separator("Comparison")
    for name, r in results.items():
        log(f"{name:>8}: stage1={r['candidates']:>3} candidates, stage2={r['final']:>3} papers")

    # Show per-model picks
    all_ids = set()
    for r in results.values():
        for p in r["papers"]:
            all_ids.add(p["arxiv_id"])

    log(f"\nUnion of all picks: {len(all_ids)} unique papers")
    log("")
    log(f"{'Paper ID':<16} {'Title':<60} {'haiku':>6} {'sonnet':>6} {'opus':>6}")
    log("-" * 110)

    paper_lookup = {p["arxiv_id"]: p["title"] for p in papers}
    for aid in sorted(all_ids):
        title = paper_lookup.get(aid, "?")[:58]
        cols = []
        for name in ["haiku", "sonnet", "opus"]:
            if name not in results:
                cols.append("  ERR")
                continue
            paper_match = [p for p in results[name]["papers"] if p["arxiv_id"] == aid]
            if paper_match:
                tier = paper_match[0].get("relevance_tier", "?")[0].upper()
                cols.append(f"    {tier}")
            else:
                cols.append("     ")
        log(f"{aid:<16} {title:<60} {''.join(cols)}")


if __name__ == "__main__":
    main()
