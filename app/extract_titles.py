#!/usr/bin/env python3
"""Extract arxiv_id + title lines from a fetched papers JSON file."""

import json
import sys

papers = json.load(open(sys.argv[1]))
for p in papers:
    published = p.get("published", "?")
    print(f"{p['arxiv_id']}  {p['title']}  (published: {published})")
