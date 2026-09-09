import json
import re
import time
from urllib.parse import urljoin, urlparse
from typing import Any
from playwright.sync_api import Browser, Page, TimeoutError as PlaywrightTimeoutError

from app.scraper.selectors import DETAIL_XPATHS, READER_XPATHS, ONLINE_LABELS, NEXT_LABELS, LAST_LABELS, UI_SELECTORS
from app.models import Book, BookPage

PAGE_TIMEOUT_MS = 45_000
DOM_SETTLE_MS = 700
MAX_RETRIES = 3
MAX_PAGES = 2000

def norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def norm_label(text: str) -> str:
    text = (text or "").replace("\u200c", " ").replace("\u200f", "").replace("\u202a", "").replace("\u202c", "")
    return norm_space(text).casefold()

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
    results = []
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
    last = None
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
    loc = visible_locator(page, DETAIL_XPATHS["online_reading"])
    if loc is not None:
        href = loc.get_attribute("href") or ""
        if href and not href.startswith("javascript:"):
            return urljoin(page.url, href)

    for label in ONLINE_LABELS:
        try:
            loc = page.locator(f"xpath=//a[normalize-space(.)={json.dumps(label, ensure_ascii=False)}]").first
            if loc.count():
                href = loc.get_attribute("href") or ""
                if href and not href.startswith("javascript:"):
                    return urljoin(page.url, href)
        except Exception:
            continue

    wanted = {norm_label(x) for x in ONLINE_LABELS}
    for item in page_hrefs(page):
        if norm_label(item["text"]) in wanted and not item["href"].startswith("javascript:"):
            return item["href"]
    return ""

def extract_clean_container_text(page: Page, root_xpath: str, *, content_mode: bool) -> str:
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
                clone.querySelectorAll('ul, ol').forEach(ul => {
                  const txt = (ul.innerText || '').trim().toLowerCase();
                  if (/\\b(first|prev|next|last)\\b/.test(txt) || /پہلے|اگلا|آخری|سابقہ/.test(txt)) {
                    ul.remove();
                  }
                });
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
    text = extract_clean_container_text(page, DETAIL_XPATHS["book_detail_root"], content_mode=False)
    if len(text) >= 100:
        return text
    for sel in ["main", "article", "[role='main']"]:
        try:
            loc = page.locator(sel).first
            if loc.count():
                return norm_space(loc.inner_text(timeout=10_000))
        except Exception:
            continue
    return norm_space(page.locator("body").inner_text(timeout=PAGE_TIMEOUT_MS))

def extract_reader_text(page: Page) -> str:
    for xpath in ["//*[@id='element']", "//section[@id='jumbo']//div[@id='element']"]:
        text = extract_clean_container_text(page, xpath, content_mode=True)
        if len(text) >= 80:
            return text
    text = extract_clean_container_text(page, READER_XPATHS["reader_root"], content_mode=True)
    if len(text) >= 80:
        return text
    for sel in ["main article", "article", "[role='main']"]:
        try:
            loc = page.locator(sel).first
            if loc.count():
                return norm_space(loc.inner_text(timeout=10_000))
        except Exception:
            continue
    return norm_space(page.locator("body").inner_text(timeout=PAGE_TIMEOUT_MS))

def _value_after_label(text: str, labels: list[str], stop_labels: list[str]) -> str:
    flat = norm_space(text)
    label_pattern = "|".join(re.escape(x) for x in labels)
    stop_pattern = "|".join(re.escape(x) for x in stop_labels)
    if stop_pattern:
        pat = rf"(?:{label_pattern})\s*[:：-]\s*(.*?)(?=\s*(?:{stop_pattern})\s*[:：-]|$)"
    else:
        pat = rf"(?:{label_pattern})\s*[:：-]\s*(.+)$"
    m = re.search(pat, flat, flags=re.I)
    if not m:
        return ""
    value = norm_space(m.group(1))
    for ui in ["آن لائن پڑھیں", "ڈاؤن لوڈ کریں", "آڈیو چلائیں", "Online Parhein", "Download", "Listen"]:
        value = norm_space(re.sub(re.escape(ui), " ", value, flags=re.I))
    return value

def detect_page_count(text: str) -> int | None:
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

def parse_detail_metadata(text: str) -> dict:
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

def exact_feature_present(page: Page, *, text_labels=None, css_selector=None) -> bool:
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
    candidates = ["li.active", "li[aria-current='page']", ".active[role='link']", "[aria-current='page']"]
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if loc.count():
                n = re.search(r"\d+", norm_space(loc.inner_text(timeout=1500)))
                if n:
                    return int(n.group(0))
        except Exception:
            continue
    return fallback

def get_next_href(page: Page, current_number: int) -> str:
    target = str(current_number + 1)
    xp = f"//a[normalize-space(.)='{target}']"
    loc = visible_locator(page, xp)
    if loc is not None:
        href = loc.get_attribute("href") or ""
        if href and not href.startswith("javascript:"):
            return urljoin(page.url, href)

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
    if next_disabled(page):
        return True
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

def detail_features(page: Page):
    return (
        exact_feature_present(page, text_labels=["آڈیو سنیں", "آڈیو بک"], css_selector="audio"),
        exact_feature_present(page, text_labels=["پی ڈی ایف ڈاؤن لوڈ"]),
        exact_feature_present(page, text_labels=["ترجمہ"]),
    )

def do_scrape_book_logic(browser: Browser, db_session, book_id_pk: int, book_detail_url: str, log_cb=None):
    if not log_cb:
        log_cb = lambda msg: print(msg)
        
    context = browser.new_context(
        locale="en-US",
        viewport={"width": 1440, "height": 1000},
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    )
    page = context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT_MS)
    
    try:
        book = db_session.query(Book).get(book_id_pk)
        if not book:
            raise RuntimeError("Book not found in database")
            
        log_cb(f"Starting Book {book.book_id}")
        goto_with_retries(page, book_detail_url)
        log_cb(f"Detail page loaded")
        
        detail_title = norm_space(page.title())
        try:
            h1 = page.locator("h1").first
            if h1.count():
                h1_text = norm_space(h1.inner_text(timeout=5000))
                if h1_text:
                    detail_title = h1_text
        except:
            pass
            
        detail_text = extract_detail_text(page)
        meta = parse_detail_metadata(detail_text)
        
        book.title = detail_title
        book.language = infer_language(page.url, detail_title)
        book.author = meta["author"]
        book.publisher = meta["publisher"]
        book.total_pages = meta["page_count"]
        has_audio, has_pdf, has_translation = detail_features(page)
        
        db_session.commit()
        
        read_url = get_online_read_url(page)
        if not read_url:
            raise RuntimeError("Online Reading link not found.")
            
        log_cb("Online Reading found")
        goto_with_retries(page, read_url)
        
        verified_total = book.total_pages
        if not verified_total:
            body = norm_space(page.locator("body").inner_text(timeout=PAGE_TIMEOUT_MS))
            verified_total = detect_page_count(body)
            book.total_pages = verified_total
            db_session.commit()
            
        # load existing pages for this book to avoid re-scraping
        existing_pages = db_session.query(BookPage).filter(BookPage.book_id == book.id).all()
        existing_by_num = {p.page_number: p for p in existing_pages}
        existing_by_url = {p.page_url: p for p in existing_pages}
        visited_urls = set(existing_by_url.keys())
        
        fallback_number = 1
        scraped_count = 0
        
        for _ in range(MAX_PAGES):
            current_number = detect_page_number(page, fallback_number)
            current_url = page.url
            current_text = extract_reader_text(page)
            
            if current_url in existing_by_url:
                current_number = existing_by_url[current_url].page_number
            elif current_number in existing_by_num:
                current_number = max(existing_by_num.keys(), default=0) + 1
                
            if current_url not in existing_by_url:
                title = norm_space(page.title())
                has_audio_p = exact_feature_present(page, text_labels=["آڈیو سنیں", "آڈیو بک"], css_selector="audio")
                has_pdf_p = exact_feature_present(page, text_labels=["پی ڈی ایف ڈاؤن لوڈ"])
                has_trans_p = exact_feature_present(page, text_labels=["ترجمہ"])
                
                new_page = BookPage(
                    book_id=book.id,
                    page_number=current_number,
                    page_url=current_url,
                    page_title=title,
                    page_text=current_text,
                    page_meta_description=extract_meta_description(page),
                    page_has_audio=has_audio_p,
                    page_has_pdf=has_pdf_p,
                    page_has_translation=has_trans_p,
                    total_pages=verified_total,
                    scrape_status="SCRAPED"
                )
                db_session.add(new_page)
                db_session.commit()
                
                existing_by_num[current_number] = new_page
                existing_by_url[current_url] = new_page
                visited_urls.add(current_url)
                
                log_cb(f"Page {current_number} / {verified_total or '?'} scraped")
                scraped_count += 1
            else:
                log_cb(f"Cached Page {current_number} skipped")
                
            if verified_total is not None and current_number >= verified_total:
                break
            if detect_verified_end(page):
                break
                
            next_url = get_next_href(page, current_number)
            if next_url:
                if next_url in visited_urls:
                    raise RuntimeError("Pagination loop detected")
                old_url, old_text = page.url, current_text
                goto_with_retries(page, next_url)
                if not page_changed_after_navigation(page, old_url, old_text):
                    raise RuntimeError("Next navigation did not change page")
                fallback_number = current_number + 1
                continue
                
            btn = visible_locator(page, READER_XPATHS["next_button"])
            if btn is not None and not next_disabled(page):
                old_url, old_text = page.url, current_text
                btn.click(timeout=10_000)
                if not page_changed_after_navigation(page, old_url, old_text):
                    raise RuntimeError("Next button produced no page change")
                fallback_number = current_number + 1
                continue
                
            raise RuntimeError(f"Could not determine next page after {current_number}")
            
        book.scrape_status = "SCRAPED"
        db_session.commit()
        log_cb(f"Book {book.book_id} completed")
        return True

    except Exception as exc:
        db_session.rollback()
        raise exc
    finally:
        context.close()
