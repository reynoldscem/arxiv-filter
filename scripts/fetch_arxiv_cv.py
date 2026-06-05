#!/usr/bin/env python3
"""
Fetch daily cs.CV papers from arXiv with abstracts.

Uses the RSS feed for today's papers, or scrapes archive for historical dates.

Usage:
    python3 fetch_arxiv_cv.py                    # Fetch today's papers
    python3 fetch_arxiv_cv.py --date 2025-12-15  # Fetch specific date
    python3 fetch_arxiv_cv.py --category cs.AI   # Different category
    python3 fetch_arxiv_cv.py --output json      # Output as JSON
    python3 fetch_arxiv_cv.py --save papers.md   # Save to file
"""

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html import unescape
from typing import List, Dict, Optional


ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_RSS_URL = "https://rss.arxiv.org/rss"
ARXIV_CATCHUP_URL = "https://arxiv.org/catchup"
BATCH_SIZE = 50  # Papers per API request
REQUEST_DELAY = 5  # Seconds between API requests (be nice to arXiv)
SCRAPE_WORKERS = 8  # Parallel workers for abs page fallback
SCRAPE_DELAY = 0.5  # Delay between scrape requests per worker


def fetch_rss_ids(category: str) -> List[str]:
    """
    Fetch paper IDs from RSS feed (today's announcements).

    Returns list of arXiv IDs matching what's shown on the /new page.
    """
    url = f"{ARXIV_RSS_URL}/{category}"
    print(f"Fetching RSS feed: {url}", file=sys.stderr)

    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            xml_data = response.read().decode("utf-8")
    except Exception as e:
        print(f"Error fetching RSS feed: {e}", file=sys.stderr)
        return []

    # Parse RSS XML
    root = ET.fromstring(xml_data)

    arxiv_ids = []
    for item in root.findall(".//item"):
        link = item.find("link")
        if link is not None and link.text:
            # Extract ID from URL like http://arxiv.org/abs/2512.11015
            match = re.search(r"(\d{4}\.\d{4,5})", link.text)
            if match:
                arxiv_ids.append(match.group(1))

    print(f"Found {len(arxiv_ids)} papers in RSS feed", file=sys.stderr)
    return arxiv_ids


def fetch_historical_ids(category: str, date: datetime) -> List[str]:
    """
    Fetch paper IDs from arXiv pastweek page for a specific historical date.

    Args:
        category: arXiv category (e.g., 'cs.CV')
        date: The announcement date to fetch

    Returns list of arXiv IDs announced on that date.
    """
    # Fetch the pastweek page which groups papers by announcement date
    url = f"https://arxiv.org/list/{category}/pastweek?skip=0&show=2000"
    print(f"Fetching pastweek page: {url}", file=sys.stderr)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as response:
            html_data = response.read().decode("utf-8")
    except Exception as e:
        print(f"Error fetching pastweek page: {e}", file=sys.stderr)
        return []

    # Target date formats that appear in the page
    # e.g., "Mon, 15 Dec 2025" or "Tue, 16 Dec 2025"
    target_date_str = date.strftime("%d %b %Y").lstrip("0")  # "15 Dec 2025"
    # Also try with leading zero
    target_date_str_padded = date.strftime("%d %b %Y")  # "15 Dec 2025"

    print(f"Looking for date: {target_date_str}", file=sys.stderr)

    # Split by date headers (format: "Mon, 15 Dec 2025")
    # Date headers look like: <h3>Mon, 15 Dec 2025</h3> or similar
    date_pattern = r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s+(\d{1,2}\s+\w+\s+\d{4})'

    # Find all date sections
    sections = re.split(r'<h3[^>]*>([^<]+)</h3>', html_data)

    arxiv_ids = []
    in_target_section = False

    for i, section in enumerate(sections):
        # Check if this is a date header matching our target
        date_match = re.search(date_pattern, section)
        if date_match:
            section_date = date_match.group(1)
            # Normalize both dates for comparison
            if target_date_str in section or target_date_str_padded in section:
                in_target_section = True
                print(f"Found target date section: {section.strip()}", file=sys.stderr)
            else:
                in_target_section = False
        elif in_target_section:
            # Extract arXiv IDs from this section
            ids_in_section = re.findall(r'arXiv:(\d{4}\.\d{4,5})', section)
            arxiv_ids.extend(ids_in_section)

    # Deduplicate while preserving order
    seen = set()
    unique_ids = []
    for aid in arxiv_ids:
        if aid not in seen:
            seen.add(aid)
            unique_ids.append(aid)

    print(f"Found {len(unique_ids)} papers for {date.strftime('%Y-%m-%d')}", file=sys.stderr)
    return unique_ids


def scrape_abs_page(arxiv_id: str) -> Optional[Dict]:
    """Scrape paper details from arxiv.org/abs/ page (fallback when API is down)."""
    url = f"https://arxiv.org/abs/{arxiv_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode("utf-8")
    except Exception as e:
        print(f"  scrape {arxiv_id}: failed - {e}", file=sys.stderr)
        return None

    def meta(name):
        m = re.search(rf'<meta\s+name="{name}"\s+content="(.*?)"', html, re.DOTALL)
        return unescape(m.group(1)).strip() if m else None

    title = meta("citation_title")
    abstract = meta("citation_abstract")
    if not title or not abstract:
        print(f"  scrape {arxiv_id}: missing title/abstract", file=sys.stderr)
        return None

    authors = re.findall(r'<meta\s+name="citation_author"\s+content="(.*?)"', html)
    authors = [unescape(a).strip() for a in authors]
    # Convert "Last, First" to "First Last"
    authors = [" ".join(reversed(a.split(", ", 1))) if ", " in a else a for a in authors]

    published = meta("citation_date")
    pdf_url = meta("citation_pdf_url")

    # Extract comment from HTML table
    comment = None
    cm = re.search(r'class="tablecell comments[^"]*">(.*?)</td>', html, re.DOTALL)
    if cm:
        comment = re.sub(r'<[^>]+>', '', cm.group(1)).strip()
        comment = " ".join(comment.split())

    # Extract categories from subjects
    categories = []
    sm = re.search(r'class="primary-subject">(.*?)</span>(.*?)</td>', html, re.DOTALL)
    if sm:
        primary = re.search(r'\(([^)]+)\)', sm.group(1))
        if primary:
            categories.append(primary.group(1))
        for sec in re.finditer(r'\(([^)]+)\)', sm.group(2)):
            categories.append(sec.group(1))

    return {
        "arxiv_id": arxiv_id,
        "title": " ".join(title.split()),
        "authors": authors,
        "abstract": " ".join(abstract.split()),
        "comment": comment,
        "categories": categories,
        "published": published,
        "updated": None,
        "pdf_url": pdf_url or f"https://arxiv.org/pdf/{arxiv_id}",
        "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
    }


def scrape_papers_by_ids(arxiv_ids: List[str]) -> List[Dict]:
    """Fetch paper details by scraping abs pages in parallel."""
    print(f"Falling back to abs page scraping for {len(arxiv_ids)} papers...", file=sys.stderr)
    papers = []
    with ThreadPoolExecutor(max_workers=SCRAPE_WORKERS) as pool:
        futures = {pool.submit(scrape_abs_page, aid): aid for aid in arxiv_ids}
        for i, future in enumerate(as_completed(futures)):
            paper = future.result()
            if paper:
                papers.append(paper)
            if (i + 1) % 20 == 0:
                print(f"  scraped {i + 1}/{len(arxiv_ids)}...", file=sys.stderr)
    print(f"Scraped {len(papers)}/{len(arxiv_ids)} papers from abs pages", file=sys.stderr)
    return papers


def fetch_papers_by_ids(arxiv_ids: List[str]) -> List[Dict]:
    """
    Fetch full paper details from arXiv API by ID.
    Falls back to scraping abs pages for any IDs the API fails to return.
    """
    if not arxiv_ids:
        return []

    papers = []

    # Process in batches
    for i in range(0, len(arxiv_ids), BATCH_SIZE):
        batch_ids = arxiv_ids[i:i + BATCH_SIZE]
        id_list = ",".join(batch_ids)

        params = {
            "id_list": id_list,
            "max_results": len(batch_ids),
        }

        url = f"{ARXIV_API_URL}?{urllib.parse.urlencode(params)}"
        print(f"Fetching papers {i + 1}-{i + len(batch_ids)} of {len(arxiv_ids)}...", file=sys.stderr)

        xml_data = None
        max_attempts = 6
        for attempt in range(max_attempts):
            try:
                with urllib.request.urlopen(url, timeout=30) as response:
                    xml_data = response.read().decode("utf-8")
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < max_attempts - 1:
                    wait = 15 * (2 ** attempt)  # 15, 30, 60, 120, 240s
                    print(f"Rate limited (429), waiting {wait}s (attempt {attempt + 1}/{max_attempts})...", file=sys.stderr)
                    time.sleep(wait)
                else:
                    print(f"Error fetching batch: {e}", file=sys.stderr)
                    break
            except (TimeoutError, OSError) as e:
                if attempt < max_attempts - 1:
                    wait = 15 * (2 ** attempt)
                    print(f"Timeout/network error, waiting {wait}s (attempt {attempt + 1}/{max_attempts}): {e}", file=sys.stderr)
                    time.sleep(wait)
                else:
                    print(f"Error fetching batch after {max_attempts} attempts: {e}", file=sys.stderr)
                    break
            except Exception as e:
                print(f"Error fetching batch: {e}", file=sys.stderr)
                break
        if xml_data is None:
            continue

        # Parse Atom XML
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom"
        }

        root = ET.fromstring(xml_data)

        for entry in root.findall("atom:entry", ns):
            paper = parse_entry(entry, ns)
            if paper:
                papers.append(paper)

        # Be nice to arXiv
        if i + BATCH_SIZE < len(arxiv_ids):
            time.sleep(REQUEST_DELAY)

    # Fallback: scrape abs pages for any IDs the API missed
    fetched_ids = {p["arxiv_id"] for p in papers}
    missing_ids = [aid for aid in arxiv_ids if aid not in fetched_ids]
    if missing_ids:
        print(f"API returned {len(papers)}/{len(arxiv_ids)} papers, {len(missing_ids)} missing", file=sys.stderr)
        scraped = scrape_papers_by_ids(missing_ids)
        papers.extend(scraped)

    return papers


def parse_entry(entry: ET.Element, ns: Dict[str, str]) -> Optional[Dict]:
    """Parse a single arXiv entry into a dict."""
    id_elem = entry.find("atom:id", ns)
    title_elem = entry.find("atom:title", ns)
    summary_elem = entry.find("atom:summary", ns)
    published_elem = entry.find("atom:published", ns)
    updated_elem = entry.find("atom:updated", ns)

    if id_elem is None:
        return None

    # Extract arXiv ID
    arxiv_id = ""
    match = re.search(r"(\d{4}\.\d{4,5})", id_elem.text or "")
    if match:
        arxiv_id = match.group(1)
    else:
        return None

    # Get authors
    authors = []
    for author in entry.findall("atom:author", ns):
        name = author.find("atom:name", ns)
        if name is not None and name.text:
            authors.append(name.text.strip())

    # Get categories
    categories = []
    primary_cat = entry.find("arxiv:primary_category", ns)
    if primary_cat is not None:
        term = primary_cat.get("term")
        if term:
            categories.append(term)

    for cat in entry.findall("atom:category", ns):
        term = cat.get("term")
        if term and term not in categories:
            categories.append(term)

    # Get PDF link
    pdf_url = None
    for link in entry.findall("atom:link", ns):
        if link.get("title") == "pdf":
            pdf_url = link.get("href")
            break

    # Get comment (often contains project page / code links)
    comment_elem = entry.find("arxiv:comment", ns)
    comment = " ".join((comment_elem.text or "").split()) if comment_elem is not None else None

    # Clean up title and abstract
    title = " ".join((title_elem.text or "").split())
    abstract = " ".join((summary_elem.text or "").split())

    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "comment": comment,
        "categories": categories,
        "published": published_elem.text if published_elem is not None else None,
        "updated": updated_elem.text if updated_elem is not None else None,
        "pdf_url": pdf_url,
        "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
    }


def format_markdown(papers: List[Dict], category: str) -> str:
    """Format papers as markdown."""
    today = datetime.now().strftime("%Y-%m-%d")

    lines = [
        f"# arXiv {category} Papers - {today}",
        "",
        f"**Total papers: {len(papers)}**",
        "",
        "---",
        "",
    ]

    for i, paper in enumerate(papers, 1):
        authors_list = paper["authors"]
        if len(authors_list) > 5:
            authors = ", ".join(authors_list[:5])
            authors += f" et al. ({len(authors_list)} authors)"
        else:
            authors = ", ".join(authors_list)

        categories = ", ".join(paper["categories"])

        lines.extend([
            f"## {i}. {paper['title']}",
            "",
            f"**arXiv ID:** [{paper['arxiv_id']}]({paper['abs_url']})",
            "",
            f"**Authors:** {authors}",
            "",
            f"**Categories:** {categories}",
            "",
            "### Abstract",
            "",
            paper["abstract"],
            "",
            f"📄 [PDF]({paper['pdf_url']}) | 🔗 [arXiv]({paper['abs_url']})",
            "",
            "---",
            "",
        ])

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Fetch daily arXiv papers with abstracts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 fetch_arxiv_cv.py                      # Today's cs.CV papers
  python3 fetch_arxiv_cv.py -d 2025-12-15        # Historical date
  python3 fetch_arxiv_cv.py -c cs.AI             # Today's cs.AI papers
  python3 fetch_arxiv_cv.py -o json              # Output as JSON
  python3 fetch_arxiv_cv.py -s papers.md         # Save to file
  python3 fetch_arxiv_cv.py -c cs.LG -o json -s ml_papers.json
        """
    )
    parser.add_argument(
        "--date", "-d",
        metavar="YYYY-MM-DD",
        help="Fetch papers from a specific date (default: today)",
    )
    parser.add_argument(
        "--category", "-c",
        default="cs.CV",
        help="arXiv category to fetch (default: cs.CV)",
    )
    parser.add_argument(
        "--output", "-o",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "--save", "-s",
        metavar="FILE",
        help="Save output to file instead of stdout",
    )

    args = parser.parse_args()

    # Parse date if provided
    target_date = None
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            print(f"Invalid date format: {args.date}. Use YYYY-MM-DD.", file=sys.stderr)
            sys.exit(1)

    # Step 1: Get paper IDs
    if target_date:
        # Historical fetch
        print(f"Fetching {args.category} papers for {target_date.strftime('%Y-%m-%d')}...", file=sys.stderr)
        arxiv_ids = fetch_historical_ids(args.category, target_date)
    else:
        # Today's papers via RSS
        print(f"Fetching today's {args.category} papers from arXiv...", file=sys.stderr)
        arxiv_ids = fetch_rss_ids(args.category)

    if not arxiv_ids:
        print("No papers found (normal on weekends/holidays).", file=sys.stderr)
        # Still save empty output if -s was given, but exit 0
        if args.save:
            with open(args.save, "w") as f:
                json.dump([], f)
        sys.exit(0)

    # Step 2: Fetch full details via API
    papers = fetch_papers_by_ids(arxiv_ids)
    print(f"Successfully fetched {len(papers)} papers with abstracts", file=sys.stderr)

    # Step 3: Format output
    if args.output == "json":
        output = json.dumps(papers, indent=2, ensure_ascii=False)
    else:
        output = format_markdown(papers, args.category)

    # Step 4: Save or print
    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Saved to {args.save}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
