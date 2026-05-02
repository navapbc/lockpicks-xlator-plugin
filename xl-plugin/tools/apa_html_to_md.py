#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["beautifulsoup4>=4.12", "requests>=2.28"]
# ///
"""
Scrape an Alaska APA manual HTML page and convert it to Markdown.

Usage:
  uv run scrape_to_md.py \
    "http://dpaweb.hss.state.ak.us/manuals/apa/442/442-1_income_exclusions_which_apply_to_both_.htm" \
    -o 442-1_income_exclusions.md
"""

from __future__ import annotations

import argparse
import os
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    import html2text
except ImportError as e:
    raise SystemExit(
        "Missing dependency: html2text\n"
        "Install with: pip install html2text beautifulsoup4 requests"
    ) from e


DEFAULT_URL = "http://dpaweb.hss.state.ak.us/manuals/apa/442/442-1_income_exclusions_which_apply_to_both_.htm"


def fetch_html(url: str, timeout: int = 30) -> str:
    headers = {
        # Some older servers behave better with a browser-like UA.
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8",
        "Connection": "close",
    }

    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()

    # Let requests/better-charset detection pick encoding if not declared well.
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def make_links_absolute(soup: BeautifulSoup, base_url: str) -> None:
    """Convert relative href/src to absolute URLs."""
    for tag in soup.find_all(["a", "img", "link", "script"]):
        attr = "href" if tag.name in ("a", "link") else "src"
        if not tag.has_attr(attr):
            continue
        val = tag.get(attr)
        if not val:
            continue
        # Ignore anchors, mailto, javascript
        if val.startswith("#") or val.startswith("mailto:") or val.startswith("javascript:"):
            continue
        tag[attr] = urljoin(base_url, val)


def strip_junk(soup: BeautifulSoup) -> None:
    """Remove scripts/styles/nav-ish elements that hurt markdown conversion."""
    for el in soup(["script", "style", "noscript", "iframe"]):
        el.decompose()

    # Remove common nav/header/footer patterns if present
    for selector in [
        "header",
        "footer",
        "nav",
        ".nav",
        ".navbar",
        ".footer",
        ".header",
        "#nav",
        "#footer",
        "#header",
    ]:
        for el in soup.select(selector):
            el.decompose()


p_class_mapping = {
    "SectionTitle": "h2",
    "SubSectionTitle": "h3",
    "SubSection2Title": "h4",
    "SubSection3Title": "h5",
}

def promote_section_titles(soup: BeautifulSoup) -> None:
    """Replace <p class="SectionTitle"> with <h2> for proper heading conversion."""
    for cls, new_tag in p_class_mapping.items():
        for p in soup.find_all("p", class_=cls):
            if not p.get_text(strip=True):
                p.decompose()
                continue
            fixed_tag = soup.new_tag(new_tag)
            fixed_tag.extend(p.contents[:])
            p.replace_with(fixed_tag)


def promote_bold_spans(soup: BeautifulSoup) -> None:
    """Replace <span style="font-weight: bold;"> with <strong>."""
    for span in soup.find_all("span", style=True):
        if "font-weight: bold" in span["style"]:
            strong = soup.new_tag("strong")
            strong.extend(span.contents[:])
            span.replace_with(strong)


def prefix_expanding_spans(soup: BeautifulSoup) -> None:
    """Prefix <span class="expanding-content" data-targetname="glossary"> with a space."""
    for span in soup.find_all("span", class_="expanding-content"):
        if not span.get_text(strip=True):
            span.decompose()
            continue
        span.insert_before(" (")
        span.insert_after(")")


def strip_footer(soup: BeautifulSoup) -> None:
    """Remove the last table if it contains Previous/Next Section nav links."""
    for table in soup.find_all("table"):
        # Operate on footer tables only
        if "Previous Section" in table.get_text() or "Next Section" in table.get_text():
            # First remove the nav rows and collect remaining text into a new paragraph
            p = soup.new_tag("p")
            trows = table.find_all("tr")
            for tr in reversed(trows):
                text = tr.get_text(strip=True)
                if not text:
                    tr.decompose()
                elif "Previous Section" in text or "Next Section" in text:
                    tr.decompose()
                elif "MC" in text:
                    # Convert row into paragraph
                    tr_contents = tr.contents[:]
                    for td in tr_contents:
                        if not td.get_text(strip=True):
                            td.decompose()
                            continue
                        # print("----", td.extract())
                        if isinstance(td, str):
                            p.append(td)
                        elif td_text := td.get_text():
                            for c in td.contents:
                                if c.get_text(strip=True):
                                    if "MC" in td_text:
                                        # print("====", c)
                                        # append horizontal rule before MC section, and add label
                                        p.append(soup.new_tag("hr"))
                                        # p.append(f"TRANSMITTAL NUMBER: ")
                                        p.append(c.extract())

            if p.get_text(strip=True):
                # Insert p after the table, then remove the table
                print("++++", p)
                table.insert_after(p)
                table.decompose()

        # Remove empty tables that may be left after stripping footer
        if not table.get_text(strip=True):
            table.decompose()
            continue


def extract_main(soup: BeautifulSoup) -> BeautifulSoup:
    """
    Heuristically pick the main content node.

    If the site uses a known container, add it here.
    Fallback: use <body>.
    """
    # Common possibilities (customize if needed after inspecting HTML)
    candidates = [
        "main",
        "#content",
        "#main",
        ".content",
        ".main",
        "article",
    ]
    for sel in candidates:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            return el

    body = soup.body
    if body:
        return body

    # Rare case: no <body>
    return soup


def html_to_markdown(html_fragment: str) -> str:
    h = html2text.HTML2Text()
    h.ignore_images = False
    h.ignore_emphasis = False
    h.ignore_links = False
    h.body_width = 0  # don't wrap lines
    h.unicode_snob = True
    h.protect_links = True
    h.pad_tables = True
    md = h.handle(html_fragment)
    return md


def postprocess_markdown(md: str) -> str:
    # Normalize excessive blank lines
    md = re.sub(r"\n{4,}", "\n\n\n", md)

    # Fix spaces before punctuation sometimes created by converters
    md = re.sub(r" \n", "\n", md)

    # Remove blank lines between list items
    prev = None
    # Repeat until no more changes
    while prev != md:
        prev = md
        md = re.sub(r"(\n *[-*+] [^\n]+)\n\n(?= *[-*+] )", r"\1\n", md)

    # Optional: convert underlined titles or weird artifacts, if any
    md = md.strip() + "\n"
    return md


def derive_title(soup: BeautifulSoup) -> str | None:
    title = None
    if soup.title and soup.title.get_text(strip=True):
        title = soup.title.get_text(strip=True)
    # Sometimes documents put the visible title in the first heading
    h1 = soup.find(["h1", "h2"])
    if h1 and h1.get_text(strip=True):
        title = title or h1.get_text(strip=True)
    return title


def build_markdown_document(title: str | None, source_url: str, body_md: str) -> str:
    parts = []
    parts.append(f"*Source: {source_url}*\n")
    parts.append(f"Page title: [{title}]({source_url})\n")
    parts.append(body_md.strip())
    return "\n\n".join(parts).rstrip() + "\n"


def process_file(html_path: str, output_root: str, input_root: str) -> None:
    rel = os.path.relpath(html_path, input_root)
    parts = rel.replace("\\", "/").split("/")
    rel_folder = "/".join(parts[:-1])
    filename = parts[-1]
    base_url = f"http://dpaweb.hss.state.ak.us/manuals/apa/{rel_folder}"

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    source_url = urljoin(base_url + "/", filename.lower().replace(" ", "_").replace(".html", ".htm"))
    md_doc = to_markdown(html, base_url, filename, source_url)

    out_dir = os.path.join(output_root, rel_folder) if rel_folder else output_root
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, filename.removesuffix(".htm").removesuffix(".html") + ".md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md_doc)
    print(f"Wrote: {out_path}")


def process_html(html: str, base_url: str, output_root: str) -> None:
    filename = urlparse(base_url).path.split("/")[-1].removesuffix(".htm").removesuffix(".html")
    source_url = urljoin(base_url + "/", filename)
    md_doc = to_markdown(html, base_url, filename, source_url)

    os.makedirs(output_root, exist_ok=True)
    out_path = os.path.join(output_root, filename + ".md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md_doc)
    print(f"Wrote: {out_path}")


def to_markdown(html: str, base_url: str, filename: str, source_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    strip_junk(soup)
    prefix_expanding_spans(soup)
    promote_bold_spans(soup)
    strip_footer(soup)
    promote_section_titles(soup)
    make_links_absolute(soup, base_url)

    title = derive_title(soup)
    body_html = str(extract_main(soup))
    md_body = postprocess_markdown(html_to_markdown(body_html))
    md_doc = build_markdown_document(title or filename, source_url, md_body)
    return md_doc

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?", default=DEFAULT_URL,
                    help="URL, HTML file, or folder of HTML files")
    ap.add_argument("-o", "--output", default="md_output", help="Output folder (default: md_output)")
    args = ap.parse_args()

    input_path = args.input
    output_root = args.output

    if os.path.isdir(input_path):
        html_files = [
            os.path.join(dp, fn)
            for dp, _, fns in os.walk(input_path)
            for fn in fns
            if fn.lower().endswith((".html", ".htm"))
        ]
        print(f"Processing {len(html_files)} HTML files from: {input_path}")
        normed = os.path.normpath(input_path)
        input_root = os.path.dirname(normed) or normed
        for html_path in sorted(html_files):
            process_file(html_path, output_root, input_root=input_root)

    elif os.path.isfile(input_path):
        input_root = os.path.dirname(os.path.dirname(os.path.normpath(input_path))) or "."
        process_file(input_path, output_root, input_root=input_root)

    else:
        print(f"Fetching HTML from URL: {input_path}")
        html = fetch_html(input_path)
        process_html(html, input_path, output_root)


if __name__ == "__main__":
    main()
