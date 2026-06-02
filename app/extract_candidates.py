#!/usr/bin/env python3
"""Extract full paper data for candidate IDs only."""

import json
import sys

candidates_file = sys.argv[1]
papers_file = sys.argv[2]

candidates = json.load(open(candidates_file))
papers = json.load(open(papers_file))
selected = [p for p in papers if p["arxiv_id"] in candidates]
json.dump(selected, sys.stdout, indent=2)
