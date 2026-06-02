#!/usr/bin/env python3
"""Fetch og:image thumbnails from project pages for papers that have them.

Can be run standalone or called from cron_update.py after ingestion.

Usage:
    python3 fetch_thumbnails.py          # fetch all missing
    python3 fetch_thumbnails.py 2603.02351  # fetch for specific paper
"""

import os
import re
import subprocess
import sys
import urllib.request

# Allow importing models from the app directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import get_papers_missing_thumbnails, init_db, set_thumbnail, get_db

CODE_REPO_RE = re.compile(
    r'href=["\']?(https?://(?:github\.com|gitlab\.com|huggingface\.co)/[^"\'>\s]+)["\']?',
    re.IGNORECASE,
)


def set_code_url(arxiv_id, code_url):
    """Set the code_url for a paper."""
    conn = get_db()
    conn.execute(
        "UPDATE papers SET code_url = ? WHERE arxiv_id = ? AND code_url IS NULL",
        (code_url, arxiv_id),
    )
    conn.commit()
    conn.close()


def set_project_url(arxiv_id, project_url):
    """Set the project_url for a paper."""
    conn = get_db()
    conn.execute(
        "UPDATE papers SET project_url = ? WHERE arxiv_id = ? AND project_url IS NULL",
        (project_url, arxiv_id),
    )
    conn.commit()
    conn.close()

THUMBS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "thumbs")
URL_RE = re.compile(r'https?://\S+')
OG_IMAGE_RE = re.compile(
    r'<meta\s+(?:[^>]*?)property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
# Also match content before property
OG_IMAGE_RE2 = re.compile(
    r'<meta\s+(?:[^>]*?)content=["\']([^"\']+)["\']\s+property=["\']og:image["\']',
    re.IGNORECASE,
)


def extract_project_url(comment):
    """Extract project/webpage URL from a comment string."""
    if not comment:
        return None
    urls = URL_RE.findall(comment)
    for u in urls:
        clean = u.rstrip(".,;:)")
        if "arxiv.org" not in clean and "github.com" not in clean:
            return clean
    # Fall back to github URLs (some project pages are on github.io via github.com links)
    for u in urls:
        clean = u.rstrip(".,;:)")
        if "github.com" in clean:
            return clean
    return None


CODE_HOSTS = ("github.com", "gitlab.com", "huggingface.co")


def _is_code_url(url):
    """Check if a URL points to a code repository."""
    if ".github.io" in url:
        return False
    return any(host in url for host in CODE_HOSTS)


def _is_project_url(url):
    """Check if a URL is a project page (not a code repo, not arxiv)."""
    if "arxiv.org" in url:
        return False
    return not _is_code_url(url)


def classify_urls(*text_sources):
    """Extract and classify all URLs from text sources into project pages and code repos.

    Returns (project_urls, code_urls) — both deduplicated, in priority order.
    """
    project_urls = []
    code_urls = []
    seen = set()

    for text in text_sources:
        if not text:
            continue
        for u in URL_RE.findall(text):
            clean = u.rstrip(".,;:)")
            if clean in seen or "arxiv.org" in clean:
                continue
            seen.add(clean)
            if _is_code_url(clean):
                code_urls.append(clean)
            else:
                project_urls.append(clean)

    return project_urls, code_urls


def fetch_page(page_url):
    """Fetch a project page and return the HTML."""
    try:
        req = urllib.request.Request(page_url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; arXiv-app/1.0)"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  Failed to fetch {page_url}: {e}")
        return None


def extract_image_candidates(html, page_url):
    """Extract candidate image URLs from HTML in priority order."""
    from urllib.parse import urljoin

    # Ensure trailing slash for correct relative URL resolution
    if not page_url.endswith("/") and "." not in page_url.split("/")[-1]:
        page_url += "/"

    candidates = []
    seen = set()

    def add(url):
        if not url.startswith("http"):
            url = urljoin(page_url, url)
        if url not in seen:
            seen.add(url)
            candidates.append(url)

    # 1. og:image
    match = OG_IMAGE_RE.search(html) or OG_IMAGE_RE2.search(html)
    if match:
        add(match.group(1))

    # 2. <img> with teaser/banner/pipeline/overview keywords
    for m in re.finditer(
        r'<img\s+[^>]*src=["\']([^"\']+(?:teaser|banner|pipeline|overview|fig)[^"\']*)["\']',
        html, re.IGNORECASE,
    ):
        add(m.group(1))

    # 3. Any other <img> with image extension
    for m in re.finditer(
        r'<img\s+[^>]*src=["\']([^"\']+\.(?:png|jpg|jpeg|webp))["\']',
        html, re.IGNORECASE,
    ):
        add(m.group(1))

    return candidates


_GITHUB_NOISE = {
    "github.com/features", "github.com/nerfies", "github.com/settings",
    "github.com/login", "github.com/join", "github.com/about",
    "github.com/security", "github.com/pricing", "github.com/enterprise",
}


def extract_code_url(html, page_url):
    """Extract the first code repository URL from HTML."""
    # Prefer links with "code" or "github" anchor text
    priority = []
    rest = []
    for m in CODE_REPO_RE.finditer(html):
        url = m.group(1).rstrip(".,;:)'\"")
        # Skip asset/blob/raw URLs
        if any(x in url for x in ("/Assets/", "/blob/", "/raw/", "/tree/")):
            continue
        # Skip known noise
        if any(url.startswith("https://" + n) for n in _GITHUB_NOISE):
            continue
        # Need org/repo structure (at least 5 path segments for github)
        parts = url.rstrip("/").split("/")
        if "github.com" in url:
            if len(parts) < 5:
                continue
            # Check surrounding HTML for "code"/"github" button text
            pos = m.start()
            context = html[max(0, pos - 100):pos + len(m.group(0)) + 100].lower()
            if any(w in context for w in ("code", "github", "repository", "repo")):
                priority.append(url)
            else:
                rest.append(url)
        elif "huggingface.co" in url or "gitlab.com" in url:
            priority.append(url)

    return (priority + rest)[0] if (priority or rest) else None


def download_image(img_url, arxiv_id):
    """Download image and save to thumbs directory. Returns relative path."""
    os.makedirs(THUMBS_DIR, exist_ok=True)

    # Determine extension from URL
    ext = ".jpg"
    lower = img_url.lower()
    if ".png" in lower:
        ext = ".png"
    elif ".gif" in lower:
        ext = ".gif"
    elif ".webp" in lower:
        ext = ".webp"

    filename = f"{arxiv_id}{ext}"
    filepath = os.path.join(THUMBS_DIR, filename)

    try:
        req = urllib.request.Request(img_url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; arXiv-app/1.0)"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()

        if len(data) < 500:
            print(f"  Image too small ({len(data)} bytes), skipping")
            return None

        with open(filepath, "wb") as f:
            f.write(data)

        # Resize to max 800px wide for thumbnails
        try:
            from PIL import Image
            img = Image.open(filepath)
            if img.width > 800:
                img.thumbnail((800, 800))
                img.save(filepath, quality=85)
            final_size = os.path.getsize(filepath)
            print(f"  Saved {filename} ({final_size:,} bytes, resized)")
        except ImportError:
            try:
                subprocess.run(
                    ["convert", filepath, "-resize", "800x>", "-quality", "85", filepath],
                    check=True, capture_output=True, timeout=10,
                )
                final_size = os.path.getsize(filepath)
                print(f"  Saved {filename} ({final_size:,} bytes, resized)")
            except Exception:
                print(f"  Saved {filename} ({len(data):,} bytes, not resized)")

        return f"thumbs/{filename}"

    except Exception as e:
        print(f"  Failed to download {img_url}: {e}")
        return None


def fetch_arxiv_html_thumbnail(arxiv_id):
    """Fallback: grab figure 1 from arXiv's HTML rendering."""
    html_url = f"https://arxiv.org/html/{arxiv_id}v1"
    print(f"  {arxiv_id}: trying arXiv HTML fallback ({html_url})")
    html = fetch_page(html_url)
    if not html:
        return False

    # Find first figure image (typically figure 1 / teaser)
    # arXiv HTML uses relative paths like "2603.06374v1/x1.png"
    img_patterns = [
        # Images with teaser/overview/pipeline keywords first
        re.compile(
            r'<img\s+[^>]*src=["\']([^"\']*(?:teaser|banner|pipeline|overview|fig)[^"\']*\.(?:png|jpg|jpeg|webp))["\']',
            re.IGNORECASE,
        ),
        # Then any image (x1.png, pic/pic1_main.png, figure/fig_1.png, etc.)
        re.compile(
            r'<img\s+[^>]*src=["\']([^"\']+\.(?:png|jpg|jpeg|webp))["\']',
            re.IGNORECASE,
        ),
    ]

    from urllib.parse import urljoin
    # Don't add trailing slash — arxiv src paths already include the version dir
    # e.g. base="https://arxiv.org/html/2603.06374v1", src="2603.06374v1/x1.png"
    # urljoin replaces the last path segment, giving the correct URL
    base = html_url

    seen = set()
    candidates = []
    for pattern in img_patterns:
        for m in pattern.finditer(html):
            img_src = m.group(1)
            if img_src in seen:
                continue
            seen.add(img_src)
            # Skip tiny icons / badges
            if any(skip in img_src.lower() for skip in ("icon", "badge", "logo", "favicon", "arxiv-logo")):
                continue
            candidates.append(urljoin(base, img_src))
            if len(candidates) >= 3:
                break
        if len(candidates) >= 3:
            break

    for img_url in candidates:
        print(f"  {arxiv_id}: trying arxiv fig {img_url[-70:]}")
        thumb_path = download_image(img_url, arxiv_id)
        if thumb_path:
            set_thumbnail(arxiv_id, thumb_path)
            return True
    return False


def fetch_thumbnail_for_paper(arxiv_id, comment, abstract=None):
    """Fetch and store thumbnail, code URL, and project URL for a paper.

    Gathers URLs from comment + abstract, classifies them, then:
    1. Store any code URLs found directly
    2. Scrape project pages for code links + thumbnail
    3. If only code repo found, check it for a .github.io project page
    4. Fallback: arXiv HTML rendering for thumbnail
    """
    project_urls, code_urls = classify_urls(comment, abstract)
    got_thumb = False
    got_code = False

    # Step 1: Store any code URLs found directly in text
    for url in code_urls:
        print(f"  {arxiv_id}: found code URL: {url}")
        set_code_url(arxiv_id, url)
        got_code = True

    # Step 2: If no project page but we have a github repo, check it for a .github.io link
    if not project_urls and code_urls:
        gh_repo = next((u for u in code_urls if "github.com" in u), None)
        if gh_repo:
            html = fetch_page(gh_repo)
            if html:
                gh_page = re.search(
                    r'href=["\']?(https?://[^"\'>\s]*\.github\.io/[^"\'>\s]*)["\']?',
                    html, re.IGNORECASE,
                )
                if gh_page:
                    found = gh_page.group(1).rstrip(".,;:)")
                    print(f"  {arxiv_id}: found project page via repo: {found}")
                    project_urls.append(found)

    # Step 3: Scrape project pages for code links + thumbnail
    for project_url in project_urls:
        print(f"  {arxiv_id}: scanning project page {project_url}")
        set_project_url(arxiv_id, project_url)

        html = fetch_page(project_url)
        if not html:
            continue

        # Extract code URL from the page
        if not got_code:
            code_url = extract_code_url(html, project_url)
            if code_url:
                print(f"  {arxiv_id}: found code: {code_url}")
                set_code_url(arxiv_id, code_url)
                got_code = True

        # Try thumbnail from project page
        if not got_thumb:
            candidates = extract_image_candidates(html, project_url)
            for img_url in candidates:
                print(f"  {arxiv_id}: trying {img_url[:80]}")
                thumb_path = download_image(img_url, arxiv_id)
                if thumb_path:
                    set_thumbnail(arxiv_id, thumb_path)
                    got_thumb = True
                    break
            if candidates and not got_thumb:
                print(f"  {arxiv_id}: all {len(candidates)} project page images failed")

    # Step 4: Fallback — arXiv HTML rendering (figure 1)
    if not got_thumb:
        got_thumb = fetch_arxiv_html_thumbnail(arxiv_id)

    return got_thumb or got_code


def fetch_all_missing():
    """Fetch thumbnails for all papers that have project pages but no thumbnail."""
    papers = get_papers_missing_thumbnails()
    print(f"Found {len(papers)} papers missing thumbnails")
    success = 0
    for p in papers:
        if fetch_thumbnail_for_paper(p["arxiv_id"], p["comment"], p.get("abstract")):
            success += 1
    print(f"Fetched {success}/{len(papers)} thumbnails")
    return success


def fetch_for_id(arxiv_id):
    """Fetch thumbnail for a specific paper by ID."""
    conn = get_db()
    row = conn.execute(
        "SELECT arxiv_id, comment, abstract FROM papers WHERE arxiv_id = ?", (arxiv_id,)
    ).fetchone()
    conn.close()
    if not row:
        print(f"Paper {arxiv_id} not found")
        return False
    return fetch_thumbnail_for_paper(row["arxiv_id"], row["comment"], row["abstract"])


if __name__ == "__main__":
    init_db()
    if len(sys.argv) > 1:
        for arxiv_id in sys.argv[1:]:
            fetch_for_id(arxiv_id)
    else:
        fetch_all_missing()
