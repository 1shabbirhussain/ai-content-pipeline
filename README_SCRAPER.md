# Dawat-e-Islami Book Scraper

This is the **scraping-only** stage of the AEO project. It does not use an AI API.

## What it does

1. Reads `input_books.xlsx`.
2. Processes only books whose `Status` is `Pending`.
3. Opens each Detail Page.
4. Extracts raw/verified Detail Page text and metadata.
5. Finds the real `آن لائن پڑھیں` / Online Reading link.
6. Opens the Read Online reader.
7. Walks through every page sequentially, validating navigation.
8. Stores the raw page text, URL, title, page number, and feature flags.
9. Saves a resumable JSON cache after each page.
10. Writes a raw scraping workbook `scraped_books.xlsx`, one sheet per book.
11. Changes input status from `Pending` to `Scraped` only after successful full-book extraction.

The later AI stage can consume `scraped_books.xlsx` or the JSON cache. It is intentionally separate from this scraper.

## Setup

Run from this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Run

Headless:

```bash
python3 scrape_books.py
```

With a visible browser for testing:

```bash
python3 scrape_books.py --headed
```

Test only one book:

```bash
python3 scrape_books.py --book-id 1 --headed
```

## Output

- `scraped_books.xlsx` = raw structured scraping data, one sheet per book.
- `scrape_cache/BOOK_ID.json` = resumable cache.
- `scrape_log.jsonl` = one log entry per attempted book.

## Important

The scraper deliberately does not generate summaries, FAQs, JSON-LD, meta titles, or meta descriptions. Those are reserved for the later batch-AI stage.

Also, it does not mark a book `Completed`; it marks a successfully scraped book `Scraped`. The later AI stage can then use `Scraped` as its input state.
