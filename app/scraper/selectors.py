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
