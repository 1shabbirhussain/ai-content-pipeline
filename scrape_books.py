#!/usr/bin/env python3
"""
Dawat-e-Islami Books Library raw scraper (no AI).

Purpose
-------
Collect only verified source data from the Dawat-e-Islami Books Library so a
separate AI batch stage can later generate AEO/SEO content.

Input
-----
input_books.xlsx
  Required columns: Book_ID, Detail_URL, Status

Output
------
scraped_books.xlsx
  One sheet per book, one detail row + one row per online-reading page.

Resume
------
Per-book JSON is written to scrape_cache/<Book_ID>.json after every page.
Existing complete books are not scraped again. Incomplete books resume from
cache and the raw workbook is rebuilt without duplicate sheets.

Notes
-----
This is deliberately deterministic: no AI calls, no LLM, no summaries.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from playwright.sync_api import Browser, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

BASE_DIR = Path(__file__).resolve().parent
INPUT_XLSX = BASE_DIR / "input_books.xlsx"
RAW_OUTPUT_XLSX = BASE_DIR / "scraped_books.xlsx"
CACHE_DIR = BASE_DIR / "scrape_cache"
LOG_FILE = BASE_DIR / "scrape_log.jsonl"

PAGE_TIMEOUT_MS = 45_000
DOM_SETTLE_MS = 700
MAX_RETRIES = 3
MAX_PAGES = 2000

# Verified site controls. We still keep text fallbacks for future minor layout changes.
DETAIL_XPATHS = {
    "book_detail_root": "//*[@id='book-detail']",
    "online_reading": "//*[@id='book-detail']/div/div/div[1]/div[2]/div/div[3]/span/a[1]",
}
READER_XPATHS = {
    "reader_root": "//*[@id='jumbo']",
    "next_button": "//*[@id='jumbo']/div[2]/div/div[5]/ul/li[2]/a/button",
}

ONLINE_LABELS = ["آن لائن پڑھیں", "Online Parhein", "Read Online", "Online Reading"]
NEXT_LABELS = ["Next", "اگلا", "اگلا صفحہ", "›", ">"]
LAST_LABELS = ["Last", "آخری", "آخری صفحہ"]

# Nodes inside the reader/detail page that are definitely UI rather than source content.
UI_SELECTORS = [
    "script", "style", "noscript", "svg", "header", "footer", "nav", "form",
    "button", "input", "select", "textarea", "aside", "[role='navigation']",
]


@dataclass
class PageRecord:
    page_number: int
    url: str
    title: str = ""
    page_text: str = ""
    meta_description: str = ""
    language_hint: str = ""
    verified_total_pages: int | None = None
    has_audio: bool = False
    has_pdf: bool = False
    has_translation: bool = False
    discovered_links: list[dict[str, str]] = field(default_factory=list)


@dataclass
class BookRecord:
    book_id: str
    detail_url: str
    detail_title: str = ""
    detail_text: str = ""
    detail_meta_description: str = ""
    language_hint: str = ""
    author: str = ""
    publisher: str = ""
    verified_page_count: int | None = None
    scraped_page_count: int = 0
    has_audio: bool = False
    has_pdf: bool = False
    has_translation: bool = False
    read_url: str = ""
    pages: list[PageRecord] = field(default_factory=list)
    status: str = "Pending"
    error: str = ""


def norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def norm_label(text: str) -> str:
    text = (text or "").replace("\u200c", " ").replace("\u200f", "").replace("\u202a", "").replace("\u202c", "")
    return norm_space(text).casefold()


def clean_filename_part(value: str, max_len: int = 80) -> str:
    value = re.sub(r'[\\/:?*\[\]"<>|]', "_", str(value))
    value = re.sub(r"\s+", " ", value).strip()
    return (value[:max_len] or "unknown")


def clean_sheet_name(title: str, book_id: str) -> str:
    name = re.sub(r"[\\/?*\[\]:]", " ", title or "")
    name = re.sub(r"\s+", " ", name).strip() or f"Book_{book_id}"
    return name[:31]


def unique_sheet_name(wb: Workbook, title: str, book_id: str, existing_name: str | None = None) -> str:
    """Create a deterministic unique Excel sheet name."""
    base = clean_sheet_name(title, book_id)
    if existing_name and existing_name in wb.sheetnames:
        return existing_name
    if base not in wb.sheetnames:
        return base
    suffix = f"_{book_id}"
    candidate = (base[: 31 - len(suffix)] + suffix)[:31]
    n = 2
    while candidate in wb.sheetnames:
        suffix2 = f"_{book_id}_{n}"
        candidate = (base[: 31 - len(suffix2)] + suffix2)[:31]
        n += 1
    return candidate


def infer_language(url: str, title: str = "") -> str:
    parts = [p for p in urlparse(url).path.split("/") if p]
    if parts and len(parts[0]) <= 6:
        return parts[0]
    if re.search(r"[\u0600-\u06ff]", title):
        return "ar/ur"
    if re.search(r"[A-Za-z]", title):
        return "latin"
    return "unknown"


def extract_meta_description(page: Page) -> str:
    try:
        return norm_space(page.locator('meta[name="description"]').get_attribute("content") or "")
    except Exception:
        return ""


def page_hrefs(page: Page) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    try:
        anchors = page.locator("a")
        for i in range(min(anchors.count(), 500)):
            a = anchors.nth(i)
            try:
                href = a.get_attribute("href") or ""
                if not href:
                    continue
                text = norm_space(a.inner_text(timeout=1200))
                results.append({"text": text, "href": urljoin(page.url, href)})
            except Exception:
                continue
    except Exception:
        pass
    return results


def goto_with_retries(page: Page, url: str) -> None:
    last: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
            page.wait_for_timeout(DOM_SETTLE_MS)
            return
        except Exception as exc:
            last = exc
            if attempt < MAX_RETRIES:
                time.sleep(1.5 * attempt)
    raise RuntimeError(f"Failed to load URL: {url} ({last})")


def visible_locator(page: Page, xpath: str):
    loc = page.locator(f"xpath={xpath}").first
    return loc if loc.count() and loc.is_visible() else None


def get_online_read_url(page: Page) -> str:
    # Exact XPath first: this is the actual control observed on the site.
    loc = visible_locator(page, DETAIL_XPATHS["online_reading"])
    if loc is not None:
        href = loc.get_attribute("href") or ""
        if href and not href.startswith("javascript:"):
            return urljoin(page.url, href)

    # Safe exact-text fallback.
    for label in ONLINE_LABELS:
        try:
            loc = page.locator(f"xpath=//a[normalize-space(.)={json.dumps(label, ensure_ascii=False)}]").first
            if loc.count():
                href = loc.get_attribute("href") or ""
                if href and not href.startswith("javascript:"):
                    return urljoin(page.url, href)
        except Exception:
            continue

    # Final fallback by normalized visible anchor text.
    wanted = {norm_label(x) for x in ONLINE_LABELS}
    for item in page_hrefs(page):
        if norm_label(item["text"]) in wanted and not item["href"].startswith("javascript:"):
            return item["href"]
    return ""


def extract_clean_container_text(page: Page, root_xpath: str, *, content_mode: bool) -> str:
    """Clone a verified root, strip UI nodes, and return clean source text.

    The reader's verified root is #jumbo. We intentionally extract from the
    cloned DOM so navigation/header/footer are removed without altering the live
    reader. For content_mode, known reader controls and breadcrumb/title chrome
    are removed; actual article headings/paragraphs remain.
    """
    loc = visible_locator(page, root_xpath)
    if loc is None:
        return ""

    try:
        return norm_space(loc.evaluate(
            """
            (root, content_mode) => {
              const clone = root.cloneNode(true);
              const remove = %s;
              for (const sel of remove) {
                clone.querySelectorAll(sel).forEach(e => e.remove());
              }
              if (content_mode) {
                // Reader navigation block observed before the source content.
                clone.querySelectorAll('ul, ol').forEach(ul => {
                  const txt = (ul.innerText || '').trim().toLowerCase();
                  if (/\\b(first|prev|next|last)\\b/.test(txt) || /پہلے|اگلا|آخری|سابقہ/.test(txt)) {
                    ul.remove();
                  }
                });
                // Remove obvious site search/breadcrumb/tool chrome but keep headings and paragraphs.
                clone.querySelectorAll('[class*="breadcrumb" i], [class*="search" i], [class*="share" i], [class*="social" i]').forEach(e => e.remove());
              }
              return clone.innerText || clone.textContent || '';
            }
            """ % json.dumps(UI_SELECTORS, ensure_ascii=False),
            content_mode,
        ))
    except Exception:
        try:
            return norm_space(loc.inner_text(timeout=PAGE_TIMEOUT_MS))
        except Exception:
            return ""


def extract_detail_text(page: Page) -> str:
    # Prefer the verified detail root; this strips global header/footer from the raw data.
    text = extract_clean_container_text(page, DETAIL_XPATHS["book_detail_root"], content_mode=False)
    if len(text) >= 100:
        return text
    # Fallback for layout changes.
    for sel in ["main", "article", "[role='main']"]:
        try:
            loc = page.locator(sel).first
            if loc.count():
                return norm_space(loc.inner_text(timeout=10_000))
        except Exception:
            continue
    return norm_space(page.locator("body").inner_text(timeout=PAGE_TIMEOUT_MS))


def extract_reader_text(page: Page) -> str:
    # 1. Exact content container inside #jumbo: <div id="element"> contains ONLY book text
    # without the category dropdown, navigation buttons, or breadcrumbs.
    for xpath in ["//*[@id='element']", "//section[@id='jumbo']//div[@id='element']"]:
        text = extract_clean_container_text(page, xpath, content_mode=True)
        if len(text) >= 80:
            return text

    # 2. Fallback to full #jumbo root with UI cleaning
    text = extract_clean_container_text(page, READER_XPATHS["reader_root"], content_mode=True)
    if len(text) >= 80:
        return text

    # 3. Generic fallback
    for sel in ["main article", "article", "[role='main']"]:
        try:
            loc = page.locator(sel).first
            if loc.count():
                return norm_space(loc.inner_text(timeout=10_000))
        except Exception:
            continue
    return norm_space(page.locator("body").inner_text(timeout=PAGE_TIMEOUT_MS))


def _value_after_label(text: str, labels: list[str], stop_labels: list[str]) -> str:
    """Extract a metadata value with a hard boundary at the next metadata label."""
    flat = norm_space(text)
    label_pattern = "|".join(re.escape(x) for x in labels)
    stop_pattern = "|".join(re.escape(x) for x in stop_labels)
    # The site places several metadata fields inline on the same rendered line,
    # so the stop condition must work whether or not punctuation/whitespace is present.
    if stop_pattern:
        pat = rf"(?:{label_pattern})\s*[:：-]\s*(.*?)(?=\s*(?:{stop_pattern})\s*[:：-]|$)"
    else:
        pat = rf"(?:{label_pattern})\s*[:：-]\s*(.+)$"
    m = re.search(pat, flat, flags=re.I)
    if not m:
        return ""
    value = norm_space(m.group(1))
    # Strip UI text that sits between author and publisher on the current site.
    for ui in ["آن لائن پڑھیں", "ڈاؤن لوڈ کریں", "آڈیو چلائیں", "Online Parhein", "Download", "Listen"]:
        value = norm_space(re.sub(re.escape(ui), " ", value, flags=re.I))
    return value


def detect_page_count(text: str) -> int | None:
    # The site uses both plain "آن" and combining-mark "آن" and the UI phrase
    # is "آن لائن پڑھیں صفحات" (not "پڑھنے").
    online = r"(?:آن|آن)\s+لائن\s+پڑھ[یں]*"
    patterns = [
        rf"{online}\s+صفحات\s*[:：]?\s*(\d+)",
        r"online\s+reading\s+pages\s*[:：]?\s*(\d+)",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            return int(m.group(1))
    return None


def parse_detail_metadata(text: str) -> dict[str, Any]:
    labels = ["مصنف", "مصنفہ", "مصنفین", "Author", "Authors"]
    stop = [
        "ناشر", "پبلشر", "اشاعت کنندہ", "Publisher", "تاریخ اشاعت", "Publication Date",
        "آن لائن پڑھیں", "ڈاؤن لوڈ کریں", "آڈیو چلائیں",
        "کیٹیگری", "Category", "آن لائن پڑھیں صفحات", "پی ڈی ایف صفحات", "PDF Pages",
        "ISBN", "ISBN نمبر", "دیگر لینگوجز", "Other Languages", "کتاب کے بارے میں",
    ]
    author = _value_after_label(text, labels, stop)
    publisher = _value_after_label(text, ["ناشر", "پبلشر", "اشاعت کنندہ", "Publisher"], [
        "تاریخ اشاعت", "Publication Date", "کیٹیگری", "Category",
        "آن لائن پڑھیں صفحات", "پی ڈی ایف صفحات", "PDF Pages", "ISBN", "ISBN نمبر",
        "دیگر لینگوجز", "Other Languages", "کتاب کے بارے میں",
    ])
    return {
        "author": author,
        "publisher": publisher,
        "page_count": detect_page_count(text),
    }


def exact_feature_present(page: Page, *, text_labels: list[str] | None = None, css_selector: str | None = None) -> bool:
    # Strong DOM evidence first.
    if css_selector:
        try:
            if page.locator(css_selector).count() > 0:
                return True
        except Exception:
            pass
    if text_labels:
        try:
            body = norm_label(page.locator("body").inner_text(timeout=PAGE_TIMEOUT_MS))
            return any(norm_label(label) in body for label in text_labels)
        except Exception:
            pass
    return False


def detect_page_number(page: Page, fallback: int) -> int:
    # Prefer a currently active pagination marker or URL-derived numeric state.
    candidates = [
        "li.active", "li[aria-current='page']", ".active[role='link']", "[aria-current='page']"
    ]
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if loc.count():
                n = re.search(r"\d+", norm_space(loc.inner_text(timeout=1500)))
                if n:
                    return int(n.group(0))
        except Exception:
            continue
    # URL can contain the current chapter slug, but not always a number, so don't invent from it.
    return fallback


def get_next_href(page: Page, current_number: int) -> str:
    target = str(current_number + 1)
    # Exact numeric page link is most deterministic on this reader.
    xp = f"//a[normalize-space(.)='{target}']"
    loc = visible_locator(page, xp)
    if loc is not None:
        href = loc.get_attribute("href") or ""
        if href and not href.startswith("javascript:"):
            return urljoin(page.url, href)

    # Exact Next button/anchor XPath.
    for xp in [
        "//*[@id='jumbo']/div[2]/div/div[5]/ul/li[2]/a",
        "//a[normalize-space(.)='Next']",
        "//a[normalize-space(.)='اگلا']",
        "//a[normalize-space(.)='اگلا صفحہ']",
    ]:
        loc = visible_locator(page, xp)
        if loc is not None:
            href = loc.get_attribute("href") or ""
            if href and not href.startswith("javascript:"):
                href = urljoin(page.url, href)
                if href != page.url:
                    return href
    return ""


def next_disabled(page: Page) -> bool:
    btn = visible_locator(page, READER_XPATHS["next_button"])
    if btn is None:
        return False
    try:
        if btn.is_disabled():
            return True
    except Exception:
        pass
    try:
        for attr in ["disabled", "aria-disabled"]:
            val = btn.get_attribute(attr)
            if val and val.lower() in {"true", "disabled"}:
                return True
        parent = page.locator(f"xpath={READER_XPATHS['next_button']}/ancestor::a[1]")
        if parent.count():
            cls = parent.get_attribute("class") or ""
            aria = parent.get_attribute("aria-disabled") or ""
            href = parent.get_attribute("href") or ""
            if "disabled" in cls.casefold() or aria.casefold() == "true" or href in {"", "#"}:
                return True
    except Exception:
        pass
    return False


def detect_verified_end(page: Page) -> bool:
    body = norm_label(page.locator("body").inner_text(timeout=PAGE_TIMEOUT_MS))
    # A disabled Next is definitive.
    if next_disabled(page):
        return True
    # Last button/anchor being disabled is another useful signal.
    for label in LAST_LABELS:
        try:
            loc = page.locator(f"xpath=//a[normalize-space(.)={json.dumps(label, ensure_ascii=False)}]").last
            if loc.count():
                aria = (loc.get_attribute("aria-disabled") or "").casefold()
                cls = (loc.get_attribute("class") or "").casefold()
                if aria == "true" or "disabled" in cls:
                    return True
        except Exception:
            continue
    return False


def page_changed_after_navigation(page: Page, old_url: str, old_text: str, timeout_ms: int = 15000) -> bool:
    start = time.time()
    while (time.time() - start) * 1000 < timeout_ms:
        page.wait_for_timeout(DOM_SETTLE_MS)
        try:
            if page.url != old_url:
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT_MS)
                    page.locator(f"xpath={READER_XPATHS['reader_root']}").first.wait_for(state="visible", timeout=10000)
                except Exception:
                    pass
                page.wait_for_timeout(DOM_SETTLE_MS)
                return True
            new_text = extract_reader_text(page)
            if new_text and new_text != old_text:
                return True
        except Exception:
            if page.url != old_url:
                return True
    return False


def build_page_record(page: Page, page_number: int, verified_total_pages: int | None) -> PageRecord:
    text = extract_reader_text(page)
    title = norm_space(page.title())
    return PageRecord(
        page_number=page_number,
        url=page.url,
        title=title,
        page_text=text,
        meta_description=extract_meta_description(page),
        language_hint=infer_language(page.url, title),
        verified_total_pages=verified_total_pages,
        # These page-level flags are deliberately conservative; only exact/strong signals count.
        has_audio=exact_feature_present(page, text_labels=["آڈیو سنیں", "آڈیو بک"], css_selector="audio"),
        has_pdf=exact_feature_present(page, text_labels=["پی ڈی ایف ڈاؤن لوڈ"]),
        has_translation=exact_feature_present(page, text_labels=["ترجمہ"]),
        discovered_links=page_hrefs(page),
    )


def detail_features(page: Page) -> tuple[bool, bool, bool]:
    return (
        exact_feature_present(page, text_labels=["آڈیو سنیں", "آڈیو بک"], css_selector="audio"),
        exact_feature_present(page, text_labels=["پی ڈی ایف ڈاؤن لوڈ"]),
        exact_feature_present(page, text_labels=["ترجمہ"]),
    )


def scrape_book(browser: Browser, book_id: str, detail_url: str, existing: dict[str, Any] | None) -> BookRecord:
    context = browser.new_context(
        locale="en-US",
        viewport={"width": 1440, "height": 1000},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
        ),
    )
    page = context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT_MS)

    if existing:
        valid_fields = {k: v for k, v in existing.items() if k in BookRecord.__dataclass_fields__}
        rec = BookRecord(**valid_fields)
        rec.pages = [PageRecord(**p) for p in existing.get("pages", [])]
    else:
        rec = BookRecord(book_id=str(book_id), detail_url=detail_url)

    try:
        # -------------------- DETAIL PAGE --------------------
        goto_with_retries(page, detail_url)
        rec.detail_title = norm_space(page.title())
        try:
            h1 = page.locator("h1").first
            if h1.count():
                h1_text = norm_space(h1.inner_text(timeout=5000))
                if h1_text:
                    rec.detail_title = h1_text
        except Exception:
            pass

        rec.detail_text = extract_detail_text(page)
        rec.detail_meta_description = extract_meta_description(page)
        rec.language_hint = infer_language(page.url, rec.detail_title)
        meta = parse_detail_metadata(rec.detail_text)
        rec.author = meta["author"]
        rec.publisher = meta["publisher"]
        rec.verified_page_count = meta["page_count"]
        rec.has_audio, rec.has_pdf, rec.has_translation = detail_features(page)

        read_url = get_online_read_url(page)
        if not read_url:
            raise RuntimeError(
                f"Online Reading link not found. Expected XPath: {DETAIL_XPATHS['online_reading']}"
            )
        rec.read_url = read_url

        # -------------------- READER --------------------
        goto_with_retries(page, read_url)
        verified_total = rec.verified_page_count
        if not verified_total:
            body = norm_space(page.locator("body").inner_text(timeout=PAGE_TIMEOUT_MS))
            verified_total = detect_page_count(body)
        rec.verified_page_count = verified_total

        # Existing cache may contain valid rows. Build a lookup by URL and number.
        existing_by_url = {p.url: p for p in rec.pages if p.url}
        existing_by_num = {p.page_number: p for p in rec.pages}
        visited_urls = set(existing_by_url)

        fallback_number = 1
        for _ in range(MAX_PAGES):
            current_number = detect_page_number(page, fallback_number)
            current_url = page.url
            current_text = extract_reader_text(page)

            # Reconcile cached page number by URL when resuming.
            if current_url in existing_by_url:
                cached = existing_by_url[current_url]
                current_number = cached.page_number
            elif current_number in existing_by_num:
                current_number = max(existing_by_num.keys(), default=0) + 1

            if current_url not in existing_by_url:
                record = build_page_record(page, current_number, verified_total)
                if not record.page_text:
                    raise RuntimeError(f"Reader page {current_number} has no extractable source text: {current_url}")
                rec.pages.append(record)
                rec.pages.sort(key=lambda p: p.page_number)
                existing_by_num[current_number] = record
                existing_by_url[current_url] = record
                visited_urls.add(current_url)
                rec.scraped_page_count = len(rec.pages)
                save_cache(rec)
                print(f"    page {current_number}: {record.title} ({len(record.page_text)} chars)")
            else:
                print(f"    cached page {current_number}: {current_url}")

            # Strong completion checks.
            if verified_total is not None and current_number >= verified_total:
                break
            if detect_verified_end(page):
                break

            next_url = get_next_href(page, current_number)
            if next_url:
                if next_url in visited_urls:
                    raise RuntimeError(f"Pagination loop detected: next URL already visited: {next_url}")
                old_url = page.url
                old_text = current_text
                goto_with_retries(page, next_url)
                if not page_changed_after_navigation(page, old_url, old_text):
                    raise RuntimeError(f"Next navigation did not change page after {current_number}: {old_url}")
                fallback_number = current_number + 1
                continue

            # If no href exists, attempt the verified button itself as a last resort.
            btn = visible_locator(page, READER_XPATHS["next_button"])
            if btn is not None and not next_disabled(page):
                old_url, old_text = page.url, current_text
                try:
                    btn.click(timeout=10_000)
                except Exception as exc:
                    raise RuntimeError(f"Next button click failed on page {current_number}: {exc}") from exc
                if not page_changed_after_navigation(page, old_url, old_text):
                    raise RuntimeError(f"Next button produced no page change after {current_number}")
                fallback_number = current_number + 1
                continue

            raise RuntimeError(f"Could not determine next page after page {current_number}; end not verified")
        else:
            raise RuntimeError(f"Safety limit reached at {MAX_PAGES} pages")

        rec.pages.sort(key=lambda p: p.page_number)
        numbers = [p.page_number for p in rec.pages]
        if verified_total is not None:
            expected = list(range(1, verified_total + 1))
            missing = [n for n in expected if n not in numbers]
            if missing:
                raise RuntimeError(f"Book ended but missing page(s): {missing[:25]}")
            if len(numbers) != verified_total:
                raise RuntimeError(f"Scraped {len(numbers)} pages but verified total is {verified_total}")

        rec.scraped_page_count = len(rec.pages)
        rec.status = "Scraped"
        rec.error = ""
        save_cache(rec)
        return rec

    except Exception as exc:
        rec.scraped_page_count = len(rec.pages)
        rec.status = "Error"
        rec.error = str(exc)
        save_cache(rec)
        return rec
    finally:
        context.close()


def cache_path(book_id: str) -> Path:
    return CACHE_DIR / f"{clean_filename_part(book_id)}.json"


def load_cache(book_id: str) -> dict[str, Any] | None:
    p = cache_path(book_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_cache(rec: BookRecord) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    rec.scraped_page_count = len(rec.pages)
    tmp = cache_path(rec.book_id).with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(rec), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(cache_path(rec.book_id))


def append_log(rec: BookRecord) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "book_id": rec.book_id,
            "detail_url": rec.detail_url,
            "status": rec.status,
            "verified_page_count": rec.verified_page_count,
            "scraped_page_count": len(rec.pages),
            "error": rec.error,
        }, ensure_ascii=False) + "\n")


def _normalize_id(val: Any) -> str:
    s = str(val or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _sheet_matches_book(ws, rec: BookRecord) -> bool:
    try:
        if ws["A1"].value == "Book_ID":
            val_a2 = _normalize_id(ws["A2"].value)
            if val_a2 and val_a2 == _normalize_id(rec.book_id):
                return True
            val_b2 = str(ws["B2"].value or "").strip()
            if val_b2 and val_b2 == str(rec.detail_url).strip():
                return True
        base_title = clean_sheet_name(rec.detail_title, rec.book_id)
        if ws.title == base_title or ws.title.startswith(f"{base_title}_"):
            return True
    except Exception:
        pass
    return False


def write_raw_workbook(existing_records: list[BookRecord], output_path: Path) -> None:
    """Upsert sheets by Book_ID, preventing duplicates across reruns."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb = load_workbook(output_path) if output_path.exists() else Workbook()

    # Find and remove ALL existing sheets belonging to this Book_ID before upserting.
    # This also cleans legacy duplicate sheets such as "title" and "title_1".
    for rec in existing_records:
        old_sheets: list[tuple[int, str]] = []
        for ws in list(wb.worksheets):
            if _sheet_matches_book(ws, rec):
                old_sheets.append((wb.index(ws), ws.title))
        insert_at = min((idx for idx, _ in old_sheets), default=len(wb.worksheets))
        target_title = clean_sheet_name(rec.detail_title, rec.book_id)
        for _, title in old_sheets:
            if title in wb.sheetnames:
                wb.remove(wb[title])
        if "__INIT__" in wb.sheetnames and len(wb.sheetnames) == 1:
            wb.remove(wb["__INIT__"])
        ws = wb.create_sheet(target_title, min(insert_at, len(wb.worksheets)))

        headers = [
            "Book_ID", "Detail_URL", "Book_Title", "Language_Hint", "Author", "Publisher",
            "Detail_Meta_Description", "Detail_Has_Audio", "Detail_Has_PDF", "Detail_Has_Translation",
            "Page_Number", "Page_URL", "Page_Title", "Page_Text", "Page_Meta_Description",
            "Page_Has_Audio", "Page_Has_PDF", "Page_Has_Translation", "Verified_Total_Pages",
        ]
        for c, h in enumerate(headers, 1):
            cell = ws.cell(1, c, h)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(vertical="top", wrap_text=True)

        detail_row = [
            rec.book_id, rec.detail_url, rec.detail_title, rec.language_hint, rec.author, rec.publisher,
            rec.detail_meta_description, rec.has_audio, rec.has_pdf, rec.has_translation,
            "DETAIL", rec.detail_url, rec.detail_title, rec.detail_text, rec.detail_meta_description,
            rec.has_audio, rec.has_pdf, rec.has_translation, rec.verified_page_count,
        ]
        for c, v in enumerate(detail_row, 1):
            ws.cell(2, c, v)

        row = 3
        for p in sorted(rec.pages, key=lambda x: x.page_number):
            vals = [
                rec.book_id, rec.detail_url, rec.detail_title, rec.language_hint, rec.author, rec.publisher,
                rec.detail_meta_description, rec.has_audio, rec.has_pdf, rec.has_translation,
                p.page_number, p.url, p.title, p.page_text, p.meta_description,
                p.has_audio, p.has_pdf, p.has_translation, p.verified_total_pages,
            ]
            for c, v in enumerate(vals, 1):
                ws.cell(row, c, v)
            row += 1

        ws.freeze_panes = "A2"
        for c in range(1, len(headers) + 1):
            ws.column_dimensions[get_column_letter(c)].width = min(55, max(14, len(headers[c - 1]) + 2))
        for col in ["D", "N", "O"]:
            ws.column_dimensions[col].width = 75
        for r in range(2, ws.max_row + 1):
            for c in range(1, ws.max_column + 1):
                ws.cell(r, c).alignment = Alignment(vertical="top", wrap_text=True)

    # Remove any placeholder left over.
    if "__INIT__" in wb.sheetnames and len(wb.sheetnames) > 1:
        wb.remove(wb["__INIT__"])
    wb.save(output_path)


def get_pending_rows(input_path: Path) -> list[tuple[str, str]]:
    wb = load_workbook(input_path, data_only=False)
    result: list[tuple[str, str]] = []
    for ws in wb.worksheets:
        headers = {str(ws.cell(1, c).value).strip(): c for c in range(1, ws.max_column + 1)}
        if not {"Book_ID", "Detail_URL", "Status"}.issubset(headers):
            continue
        for r in range(2, ws.max_row + 1):
            status = str(ws.cell(r, headers["Status"]).value or "").strip()
            if status.casefold() != "pending":
                continue
            bid = str(ws.cell(r, headers["Book_ID"]).value or "").strip()
            url = str(ws.cell(r, headers["Detail_URL"]).value or "").strip()
            if bid and url:
                result.append((bid, url))
    return result


def update_input_status(input_path: Path, book_id: str, status: str) -> bool:
    wb = load_workbook(input_path)
    for ws in wb.worksheets:
        headers = {str(ws.cell(1, c).value).strip(): c for c in range(1, ws.max_column + 1)}
        if not {"Book_ID", "Status"}.issubset(headers):
            continue
        for r in range(2, ws.max_row + 1):
            if str(ws.cell(r, headers["Book_ID"]).value or "").strip() == str(book_id):
                ws.cell(r, headers["Status"], status)
                wb.save(input_path)
                return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(INPUT_XLSX))
    parser.add_argument("--output", default=str(RAW_OUTPUT_XLSX))
    parser.add_argument("--book-id", help="Process only one Book_ID")
    parser.add_argument("--headed", action="store_true", help="Show Chromium while scraping")
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    if not input_path.exists():
        print(f"ERROR: input workbook not found: {input_path}", file=sys.stderr)
        return 2

    pending = get_pending_rows(input_path)
    if args.book_id:
        pending = [row for row in pending if row[0] == str(args.book_id)]
    if not pending:
        print("No Pending books found.")
        return 0

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Found {len(pending)} Pending book(s).")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        try:
            for idx, (book_id, url) in enumerate(pending, 1):
                print(f"[{idx}/{len(pending)}] Book {book_id}: {url}")
                existing = load_cache(book_id)
                if existing and existing.get("status") == "Scraped":
                    rec = BookRecord(**{k: v for k, v in existing.items() if k in BookRecord.__dataclass_fields__})
                    rec.pages = [PageRecord(**p) for p in existing.get("pages", [])]
                    print(f"  cache hit: complete ({len(rec.pages)} page(s))")
                else:
                    rec = scrape_book(browser, book_id, url, existing)

                append_log(rec)
                if rec.status == "Scraped":
                    write_raw_workbook([rec], output_path)
                    if not update_input_status(input_path, book_id, "Scraped"):
                        raise RuntimeError(f"Could not update Status for Book_ID {book_id}")
                    print(f"  OK: {len(rec.pages)} page(s) scraped and saved")
                else:
                    # Keep Pending so a rerun resumes from cache; never mark completed after failure.
                    print(f"  ERROR: {rec.error}")
        finally:
            browser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
