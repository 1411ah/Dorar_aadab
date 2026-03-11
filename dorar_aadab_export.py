#!/usr/bin/env python3
"""
dorar_aadab_export.py — Export dorar.net/aadab to EPUB + Markdown
Usage:
    python dorar_aadab_export.py
    TEST_PAGES=10 python dorar_aadab_export.py
"""

import os
import re
import time
import uuid
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

# ── Config ────────────────────────────────────────────────────────────────────
START_URL   = "https://dorar.net/aadab"
REFS_URL    = "https://dorar.net/refs/aadab"
ARTICLE_URL = "https://dorar.net/article/2112"
PAGE_RE     = re.compile(r"/aadab/(\d+)")
SKIP_CRUMBS = 2
DELAY       = 0.5
TIMEOUT     = 20
TEST_PAGES  = int(os.getenv("TEST_PAGES") or 0)
OUT_DIR     = Path("output")
EPUB_PATH   = OUT_DIR / "aadab.epub"
MD_DIR      = OUT_DIR / "md"
BOOK_TITLE  = "موسوعة الآداب الشرعية"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
}

CHILDREN_NAMES = {1: "فصل", 2: "مبحث", 3: "مطلب", 4: "فرع", 5: "مسألة"}

PUA_RE  = re.compile(r"[\ue000-\uf8ff]")
SAFE_RE = re.compile(r'[\\/:*?"<>|]')

NAV_TEXT_RE = re.compile(
    r"السابق|التالي|انظر\s+أيض|الرابط\s+المختصر|مشاركة|share",
    re.I,
)

# ── ألوان العناوين في صفحة المنهج (article/2112) ─────────────────────────────
GREEN_RE   = re.compile(r"color\s*:\s*#0{0,2}80{3,4}",           re.I)  # #008000
BLUE_RE    = re.compile(r"color\s*:\s*#0{0,2}0{0,2}[Ff]{2}",    re.I)  # #0000FF
CRIMSON_RE = re.compile(r"color\s*:\s*#([Bb]22222|[Cc][Cc]0{4})", re.I) # #B22222 / #cc0000

# ── HTTP Session ──────────────────────────────────────────────────────────────
_session = requests.Session()
_session.headers.update(HEADERS)


def fetch(url: str) -> BeautifulSoup | None:
    try:
        r = _session.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        r.encoding = "utf-8"
        return BeautifulSoup(r.text, "html.parser")
    except Exception as exc:
        print(f"  [ERROR] {url}: {exc}")
        return None


# ── Data Classes ──────────────────────────────────────────────────────────────
@dataclass
class Page:
    pid:        str
    url:        str
    title:      str
    level:      int
    breadcrumb: list[str]
    body_html:  str
    footnotes:  list[tuple[str, str]]

    def epub_filename(self) -> str:
        return f"p{self.pid}.xhtml"


@dataclass
class IndexPage:
    pid:      str
    title:    str
    level:    int
    children: list[str]

    def epub_filename(self) -> str:
        return f"p{self.pid}.xhtml"


Item = Page | IndexPage


# ── Helpers ───────────────────────────────────────────────────────────────────
def _count_phrase(n: int, child_type: str) -> str:
    if n == 1:
        return f"وفيه {child_type} واحد"
    elif n == 2:
        return f"وفيه {child_type}ان"
    elif 3 <= n <= 10:
        plurals = {
            "فصل": "فصول", "مبحث": "مباحث", "مطلب": "مطالب",
            "فرع": "فروع",  "مسألة": "مسائل",
        }
        return f"وفيه {n} {plurals.get(child_type, child_type + 'ات')}"
    else:
        plurals = {
            "فصل": "فصلاً", "مبحث": "مبحثاً", "مطلب": "مطلباً",
            "فرع": "فرعاً",  "مسألة": "مسألةً",
        }
        return f"وفيه {n} {plurals.get(child_type, child_type)}"


def safe_name(s: str, maxlen: int = 80) -> str:
    s = SAFE_RE.sub("", s).replace(" ", "_")
    return s[:maxlen]


# ── Discovery ─────────────────────────────────────────────────────────────────
def discover_urls() -> list[str]:
    print(f"  جلب فهرس الروابط من {START_URL}…")
    index_soup = fetch(START_URL)
    all_ids: set[int] = set()
    if index_soup:
        for a in index_soup.find_all("a", href=True):
            m = PAGE_RE.search(a["href"])
            if m:
                all_ids.add(int(m.group(1)))
    if not all_ids:
        print("  [تحذير] لم يُعثر على روابط في صفحة الفهرس")
        return []

    base = "https://dorar.net"
    url  = f"{base}/aadab/{min(all_ids)}"
    urls, seen = [], set()

    while url:
        if url in seen:
            break
        seen.add(url)
        urls.append(url)
        print(f"  [{len(urls):>4}] {url}")
        if TEST_PAGES and len(urls) >= TEST_PAGES:
            break
        time.sleep(DELAY)
        soup = fetch(url)
        if not soup:
            break
        url = _next_url(soup, url)

    return urls


def _next_url(soup: BeautifulSoup, current: str) -> str | None:
    for a in soup.find_all("a", href=True):
        if PAGE_RE.search(a["href"]) and a.get_text(strip=True) == "التالي":
            return urljoin(current, a["href"])
    return None


# ── Parsing ───────────────────────────────────────────────────────────────────
def page_title(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1", class_="h5-responsive")
    if h1:
        return h1.get_text(strip=True)
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].split(" - ")[0].strip()
    t = soup.find("title")
    return t.get_text().split(" - ")[0].strip() if t else "بدون عنوان"


def page_breadcrumb(soup: BeautifulSoup) -> list[str]:
    bc_el = soup.find("ol", class_="breadcrumb")
    if not bc_el:
        return []
    return [li.get_text(strip=True) for li in bc_el.find_all("li") if li.get_text(strip=True)]


def extract_content(soup: BeautifulSoup, pid: str) -> tuple[str, list[tuple[str, str]]]:
    cntnt = soup.find("div", id="cntnt")
    if not cntnt:
        cntnt = soup.find("div", class_=lambda c: c and "amiri_custom_content" in c)
    if not cntnt:
        return "", []

    body = cntnt.find("div", class_=lambda c: c and "w-100" in c and "mt-4" in c)
    if not body:
        body = cntnt
    body = BeautifulSoup(str(body), "html.parser")

    # حذف روابط الشرح والتفسير
    for a in body.find_all("a", href=True):
        if "/hadith/sharh/" in a["href"] or "/tafseer/" in a["href"]:
            a.decompose()

    # حذف عناصر التنقل والزوائد
    for h3 in body.find_all("h3", id="more-titles"):
        nxt = h3.find_next_sibling("ul")
        if nxt:
            nxt.decompose()
        h3.decompose()

    for el in body.find_all("span", class_="scroll-pos"):
        el.decompose()

    for hr in body.find_all("hr"):
        nxt = hr.find_next_sibling()
        if nxt:
            nxt.decompose()
        hr.decompose()

    for a in body.find_all("a"):
        if NAV_TEXT_RE.search(a.get_text()):
            a.decompose()

    # استخراج الهوامش
    footnotes: list[tuple[str, str]] = []
    fn_n = 0
    for span in body.find_all("span", class_="tip"):
        fn_text = span.get_text(strip=True)
        fn_n += 1
        fn_id = f"fn-{pid}-{fn_n}"
        footnotes.append((fn_id, fn_text))
        anchor = BeautifulSoup(
            f'<sup id="ref-{fn_id}"><a href="#{fn_id}">[{fn_n}]</a></sup>',
            "html.parser",
        )
        span.replace_with(anchor)

    # تحويل span classes
    for span in body.find_all("span"):
        cls = set(span.get("class", []))
        for a in span.find_all("a"):
            a.decompose()
        txt = span.get_text(strip=True)

        if "aaya" in cls:
            span.replace_with(f"﴿{txt}﴾")
        elif "hadith" in cls:
            span.replace_with(f"«{txt}»")
        elif "sora" in cls:
            span.replace_with(PUA_RE.sub("", txt))
        elif "title-2" in cls:
            span.replace_with(BeautifulSoup(f"<h4>{txt}</h4>", "html.parser"))
        elif "title-1" in cls:
            span.replace_with(BeautifulSoup(f"<h5>{txt}</h5>", "html.parser"))

    return body.decode_contents(), footnotes


# ── الصفحات الخاصة ────────────────────────────────────────────────────────────
def scrape_special_page(url: str, pid: str, title: str, level: int) -> Page | None:
    print(f"  special [{pid}]: {url}")
    soup = fetch(url)
    if not soup:
        return None

    real_title = page_title(soup)
    if real_title and real_title != "بدون عنوان":
        title = real_title

    if "/refs/" in url:
        body_html  = _extract_refs_content(soup)
        footnotes  = []
    else:
        body_html, footnotes = _extract_article_content(soup, pid)

    bc = [BOOK_TITLE, title]
    return Page(pid=pid, url=url, title=title, level=level,
                breadcrumb=bc, body_html=body_html, footnotes=footnotes)


def _normalize_article_html(body: BeautifulSoup) -> None:
    """
    تحويل العناوين ذات inline styles في صفحة المنهج:
      أخضر  #008000  → <h3>  (عنوان رئيسي)
      أزرق  #0000FF  → <h4>  (مرحلة / قسم)
      أحمر  #B22222 / #cc0000 → <h5>  (عنوان كتاب)
    """
    for strong in body.find_all("strong"):
        style = strong.get("style", "")
        txt   = strong.get_text(strip=True)
        if not txt:
            continue
        if GREEN_RE.search(style):
            strong.replace_with(BeautifulSoup(f"<h3>{txt}</h3>", "html.parser"))
        elif BLUE_RE.search(style):
            strong.replace_with(BeautifulSoup(f"<h4>{txt}</h4>", "html.parser"))
        elif CRIMSON_RE.search(style):
            strong.replace_with(BeautifulSoup(f"<h5>{txt}</h5>", "html.parser"))

    for p in body.find_all("p"):
        p.attrs = {}


def _extract_article_content(soup: BeautifulSoup, pid: str) -> tuple[str, list]:
    cntnt = soup.find("div", id="cntnt")
    if not cntnt:
        return "", []

    body = BeautifulSoup(str(cntnt), "html.parser")

    for tag in body.find_all(["nav", "header", "footer", "script", "style", "form"]):
        tag.decompose()
    for a in body.find_all("a"):
        if NAV_TEXT_RE.search(a.get_text()):
            a.decompose()

    _normalize_article_html(body)

    footnotes: list[tuple[str, str]] = []
    fn_n = 0
    for span in body.find_all("span", class_="tip"):
        fn_text = span.get_text(strip=True)
        fn_n += 1
        fn_id = f"fn-{pid}-{fn_n}"
        footnotes.append((fn_id, fn_text))
        anchor = BeautifulSoup(
            f'<sup id="ref-{fn_id}"><a href="#{fn_id}">[{fn_n}]</a></sup>',
            "html.parser",
        )
        span.replace_with(anchor)

    return body.decode_contents(), footnotes


def _extract_refs_content(soup: BeautifulSoup) -> str:
    """
    استخراج المراجع وتنسيقها:
    **عنوان الكتاب** — المؤلف: X | الناشر: Y | الطبعة: Z | سنة الطبع: W
    """
    cntnt = soup.find("div", id="cntnt")
    if not cntnt:
        return ""

    body = cntnt.find("div", class_=lambda c: c and "w-100" in c and "mt-4" in c)
    if not body:
        body = cntnt

    body = BeautifulSoup(str(body), "html.parser")

    for ul in body.find_all("ul", class_="dorar_v_menu"):
        ul.decompose()
    for tag in body.find_all(["script", "style", "nav"]):
        tag.decompose()

    out_parts = []
    for article in body.find_all("article"):
        # عنوان الكتاب
        h5    = article.find("h5")
        title = h5.get_text(strip=True) if h5 else ""

        # تفاصيل: label: value مفصولة بـ |
        details = []
        for div in article.find_all("div", class_="d-block"):
            for strong in div.find_all("strong"):
                label = ""
                value = ""
                # النص قبل الـ span هو الـ label
                for child in strong.children:
                    if isinstance(child, NavigableString):
                        t = child.strip().rstrip(":")
                        if t:
                            label = t
                    elif hasattr(child, "get_text"):
                        value = child.get_text(strip=True)
                if label and value:
                    details.append(f"{label}: {value}")
                elif label:
                    details.append(label)

        details_str = " | ".join(details)
        sep   = " — " if details_str else ""
        block = f"<p><strong>{title}</strong>{sep}{details_str}</p>"
        out_parts.append(block)

    return "\n\n".join(out_parts) if out_parts else body.decode_contents()


# ── Scrape All ────────────────────────────────────────────────────────────────
def scrape_all() -> list[Page]:
    urls = discover_urls()
    print(f"\n{len(urls)} pages found. Parsing content…\n")
    pages: list[Page] = []

    for i, url in enumerate(urls, 1):
        pid  = f"{i:05d}"
        print(f"  parse {pid}: {url}")
        soup = fetch(url)
        if not soup:
            continue
        time.sleep(DELAY)

        title = page_title(soup)
        bc    = page_breadcrumb(soup)
        if not bc or bc[-1] != title:
            bc.append(title)

        depth = max(0, len(bc) - SKIP_CRUMBS - 1)
        level = min(depth + 1, 6)

        body_html, footnotes = extract_content(soup, pid)
        pages.append(Page(pid, url, title, level, bc, body_html, footnotes))

    return pages


# ── Build Document Order ──────────────────────────────────────────────────────
def build_document(pages: list[Page]) -> list[Item]:
    section_children: dict[tuple, list[str]] = defaultdict(list)
    for p in pages:
        ancestors = p.breadcrumb[SKIP_CRUMBS:]
        for depth in range(min(len(ancestors) - 1, 3)):
            parent_key = tuple(ancestors[:depth + 1])
            child_name = ancestors[depth + 1] if depth + 1 < len(ancestors) else p.title
            kids = section_children[parent_key]
            if child_name not in kids:
                kids.append(child_name)

    seen_idx: set[tuple] = set()
    idx_n = 0
    result: list[Item] = []

    for p in pages:
        ancestors = p.breadcrumb[SKIP_CRUMBS:]
        for depth in range(min(len(ancestors) - 1, 3)):
            key   = tuple(ancestors[:depth + 1])
            level = depth + 1
            if key not in seen_idx:
                seen_idx.add(key)
                idx_n += 1
                result.append(
                    IndexPage(
                        pid      = f"idx{idx_n:04d}",
                        title    = ancestors[depth],
                        level    = level,
                        children = section_children[key],
                    )
                )
        result.append(p)

    return result


# ── Markdown Export ───────────────────────────────────────────────────────────
def html_to_md(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    def walk(node) -> str:
        if isinstance(node, NavigableString):
            return str(node)
        if not isinstance(node, Tag):
            return ""
        name = node.name
        if name in ("script", "style"):
            return ""
        if name == "h3":
            return f"\n\n### {node.get_text(strip=True)}\n\n"
        if name == "h4":
            return f"\n\n#### {node.get_text(strip=True)}\n\n"
        if name == "h5":
            return f"\n\n##### {node.get_text(strip=True)}\n\n"
        if name == "p":
            inner = "".join(walk(c) for c in node.children)
            return f"\n\n{inner.strip()}\n\n"
        if name in ("ul", "ol"):
            items = [f"- {li.get_text(strip=True)}" for li in node.find_all("li")]
            return "\n" + "\n".join(items) + "\n\n"
        if name == "br":
            return "  \n"
        if name == "sup":
            return node.get_text()
        if name == "strong":
            txt = node.get_text(strip=True)
            return f"**{txt}**" if txt else ""
        return "".join(walk(c) for c in node.children)

    md = walk(soup)
    return re.sub(r"\n{3,}", "\n\n", md).strip()


def export_markdown(items: list[Item]) -> None:
    MD_DIR.mkdir(parents=True, exist_ok=True)
    section_dirs: dict[tuple, Path] = {}

    for item in items:
        if not isinstance(item, Page):
            continue
        ancestors = item.breadcrumb[SKIP_CRUMBS:]
        for d in range(len(ancestors) - 1):
            key = tuple(ancestors[:d + 1])
            if key not in section_dirs:
                parts = [safe_name(s) for s in ancestors[:d + 1]]
                section_dirs[key] = MD_DIR.joinpath(*parts)

        parts  = [safe_name(s) for s in ancestors[:-1]]
        folder = MD_DIR.joinpath(*parts) if parts else MD_DIR
        folder.mkdir(parents=True, exist_ok=True)

        fname    = f"{item.pid}_{safe_name(item.title)}.md"
        hashes   = "#" * item.level
        md       = html_to_md(item.body_html)
        fn_block = ""
        if item.footnotes:
            lines    = [f"[^{fid.split('-')[-1]}]: {txt}" for fid, txt in item.footnotes]
            fn_block = "\n\n---\n\n" + "\n".join(lines)
        content = (
            f"{hashes} {item.title}\n\n"
            f"> المصدر: {item.url}\n\n"
            f"{md}{fn_block}\n"
        )
        (folder / fname).write_text(content, encoding="utf-8")

    for item in items:
        if not isinstance(item, IndexPage):
            continue
        matched = None
        for key, dpath in section_dirs.items():
            if len(key) == item.level and key[-1] == item.title:
                matched = dpath
                break
        if matched is None:
            matched = MD_DIR / safe_name(item.title)
        matched.mkdir(parents=True, exist_ok=True)
        hashes     = "#" * item.level
        child_type = CHILDREN_NAMES.get(item.level, "قسم")
        phrase     = _count_phrase(len(item.children), child_type)
        bullets    = "\n".join(f"{i}. {c}" for i, c in enumerate(item.children, 1))
        content    = f"{hashes} {item.title}\n\n{phrase}:\n\n{bullets}\n"
        (matched / "_index.md").write_text(content, encoding="utf-8")

    print(f"  → Markdown → {MD_DIR}")


# ── EPUB CSS ──────────────────────────────────────────────────────────────────
EPUB_CSS = """\
@charset "UTF-8";
body {
    direction: rtl;
    font-family: Amiri, "Traditional Arabic", "Scheherazade New", Arial, sans-serif;
    font-size: 1em;
    line-height: 1.9;
    margin: 1em 2em;
    color: #333;
}
h1, h2, h3, h4, h5, h6 {
    color: #2c3e50;
    margin: 1em 0 0.4em;
    font-weight: bold;
}
h1 { font-size: 1.4em; }
h2 { font-size: 1.3em; }
h3 { font-size: 1.2em; }
h4 { font-size: 1.1em; }
h5 { font-size: 1.05em; }
h6 { font-size: 1em; }
p { margin: 0.4em 0 0.9em; text-align: justify; }
ol, ul { margin: 0.4em 0; padding-right: 1.5em; }
sup a  { color: #888; font-size: 0.8em; text-decoration: none; }
strong { font-weight: bold; }
.footnotes {
    border-top: 1px solid #ccc;
    margin-top: 2em;
    padding-top: 0.8em;
}
.footnotes ol { padding-right: 1em; }
.footnotes a  { color: #888; text-decoration: none; }
"""

_XHTML_TMPL = """\
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ar" dir="rtl">
<head>
  <meta charset="utf-8"/>
  <title>{title}</title>
  <link rel="stylesheet" type="text/css" href="../styles/book.css"/>
</head>
<body>
{body}
</body>
</html>"""


def _xhtml(title: str, body: str) -> str:
    return _XHTML_TMPL.format(title=title, body=body)


def _page_xhtml(p: Page) -> str:
    h      = f"<h{p.level}>{p.title}</h{p.level}>"
    fn_sec = ""
    if p.footnotes:
        items = "".join(
            f'<li id="{fid}"><sup>[{fid.split("-")[-1]}]</sup> {txt} '
            f'<a href="#ref-{fid}">↑</a></li>'
            for fid, txt in p.footnotes
        )
        fn_sec = f'<div class="footnotes"><ol>{items}</ol></div>'
    return _xhtml(p.title, f"{h}\n{p.body_html}\n{fn_sec}")


def _index_xhtml(ip: IndexPage) -> str:
    child_type = CHILDREN_NAMES.get(ip.level, "قسم")
    phrase     = _count_phrase(len(ip.children), child_type)
    h          = f"<h{ip.level}>{ip.title}</h{ip.level}>"
    lis        = "".join(f"<li>{c}</li>" for c in ip.children)
    return _xhtml(ip.title, f"{h}\n<p>{phrase}:</p>\n<ol>{lis}</ol>")


def _cover_xhtml(total_pages: int) -> str:
    body = (
        f'<div style="text-align:center;padding:4em 2em">'
        f"<h1>{BOOK_TITLE}</h1>"
        f"<p>عدد الصفحات: {total_pages}</p>"
        f"</div>"
    )
    return _xhtml(BOOK_TITLE, body)


# ── NCX / NAV ─────────────────────────────────────────────────────────────────
def _build_toc_tree(entries: list[tuple]) -> list[dict]:
    root: list[dict] = []
    stack: list[tuple[int, list]] = []
    for level, title, pid in entries:
        node = {"level": level, "title": title, "pid": pid, "children": []}
        while stack and stack[-1][0] >= level:
            stack.pop()
        target = stack[-1][1] if stack else root
        target.append(node)
        stack.append((level, node["children"]))
    return root


def _render_ncx(nodes: list[dict], po: list, indent: int = 4) -> list[str]:
    lines = []
    sp    = " " * indent
    for n in nodes:
        po[0] += 1
        lines += [
            f'{sp}<navPoint id="np-{n["pid"]}" playOrder="{po[0]}">',
            f'{sp}  <navLabel><text>{n["title"]}</text></navLabel>',
            f'{sp}  <content src="pages/p{n["pid"]}.xhtml"/>',
        ]
        if n["children"]:
            lines += _render_ncx(n["children"], po, indent + 2)
        lines.append(f"{sp}</navPoint>")
    return lines


def _render_nav_ol(nodes: list[dict], indent: int = 2) -> list[str]:
    if not nodes:
        return []
    sp    = " " * indent
    lines = [f"{sp}<ol>"]
    for n in nodes:
        href = f'pages/p{n["pid"]}.xhtml'
        if n["children"]:
            lines.append(f'{sp}  <li><a href="{href}">{n["title"]}</a>')
            lines += _render_nav_ol(n["children"], indent + 4)
            lines.append(f"{sp}  </li>")
        else:
            lines.append(f'{sp}  <li><a href="{href}">{n["title"]}</a></li>')
    lines.append(f"{sp}</ol>")
    return lines


def _nav_xhtml(entries: list[tuple]) -> str:
    tree  = _build_toc_tree(entries)
    inner = "\n".join(_render_nav_ol(tree))
    return f"""\
<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:epub="http://www.idpf.org/2007/ops"
      xml:lang="ar" dir="rtl">
<head><meta charset="utf-8"/><title>المحتويات</title></head>
<body>
<nav epub:type="toc" id="toc">
  <h1>المحتويات</h1>
{inner}
</nav>
</body>
</html>"""


# ── EPUB Assembly ─────────────────────────────────────────────────────────────
def export_epub(items: list[Item]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    uid   = str(uuid.uuid4())
    pages = [i for i in items if isinstance(i, Page)]

    toc_entries: list[tuple] = []
    man_items:   list[str]   = []
    spine_refs:  list[str]   = []

    with zipfile.ZipFile(EPUB_PATH, "w", zipfile.ZIP_DEFLATED) as zf:

        zf.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            '  <rootfiles>\n'
            '    <rootfile full-path="OEBPS/content.opf"'
            ' media-type="application/oebps-package+xml"/>\n'
            '  </rootfiles>\n'
            '</container>',
        )

        zf.writestr("OEBPS/styles/book.css", EPUB_CSS)
        zf.writestr("OEBPS/pages/cover.xhtml", _cover_xhtml(len(pages)))

        man_items = [
            '<item id="ncx"   href="toc.ncx"          media-type="application/x-dtbncx+xml"/>',
            '<item id="nav"   href="nav.xhtml"         media-type="application/xhtml+xml" properties="nav"/>',
            '<item id="css"   href="styles/book.css"   media-type="text/css"/>',
            '<item id="cover" href="pages/cover.xhtml" media-type="application/xhtml+xml"/>',
        ]
        spine_refs = ['<itemref idref="cover"/>']

        for item in items:
            fn  = item.epub_filename()
            iid = f"p{item.pid}"

            if isinstance(item, Page):
                zf.writestr(f"OEBPS/pages/{fn}", _page_xhtml(item))
            else:
                zf.writestr(f"OEBPS/pages/{fn}", _index_xhtml(item))

            man_items.append(
                f'<item id="{iid}" href="pages/{fn}" media-type="application/xhtml+xml"/>'
            )
            spine_refs.append(f'<itemref idref="{iid}"/>')
            toc_entries.append((item.level, item.title, item.pid))

        manifest = "\n    ".join(man_items)
        spine    = "\n    ".join(spine_refs)
        zf.writestr(
            "OEBPS/content.opf",
            f'<?xml version="1.0" encoding="utf-8"?>\n'
            f'<package xmlns="http://www.idpf.org/2007/opf" version="3.0"'
            f' unique-identifier="uid" xml:lang="ar">\n'
            f'  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            f'    <dc:title>{BOOK_TITLE}</dc:title>\n'
            f'    <dc:language>ar</dc:language>\n'
            f'    <dc:identifier id="uid">{uid}</dc:identifier>\n'
            f'  </metadata>\n'
            f'  <manifest>\n    {manifest}\n  </manifest>\n'
            f'  <spine toc="ncx" page-progression-direction="rtl">\n    {spine}\n  </spine>\n'
            f'</package>',
        )

        tree    = _build_toc_tree(toc_entries)
        po      = [0]
        ncx_pts = "\n".join(_render_ncx(tree, po))
        zf.writestr(
            "OEBPS/toc.ncx",
            f'<?xml version="1.0" encoding="utf-8"?>\n'
            f'<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN"'
            f' "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">\n'
            f'<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
            f'  <head>\n'
            f'    <meta name="dtb:uid" content="{uid}"/>\n'
            f'    <meta name="dtb:depth" content="6"/>\n'
            f'  </head>\n'
            f'  <docTitle><text>{BOOK_TITLE}</text></docTitle>\n'
            f'  <navMap>\n{ncx_pts}\n  </navMap>\n'
            f'</ncx>',
        )

        zf.writestr("OEBPS/nav.xhtml", _nav_xhtml(toc_entries))

    print(f"  → EPUB  → {EPUB_PATH}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    mode = f"TEST ({TEST_PAGES} صفحات)" if TEST_PAGES else "FULL"
    print(f"=== dorar_aadab_export  [{mode}] ===\n")

    print("1) جلب الصفحات الخاصة…")
    intro = scrape_special_page(ARTICLE_URL, "intro", "المنهج المتبع في الموسوعة", level=1)
    refs  = scrape_special_page(REFS_URL,    "refs",  "المراجع المعتمدة",           level=1)

    print("\n2) اكتشاف صفحات الموسوعة…")
    raw_pages = scrape_all()
    print(f"   {len(raw_pages)} صفحة\n")

    print("3) بناء الهيكل…")
    items = build_document(raw_pages)
    if intro:
        items = [intro] + items
    if refs:
        items = items + [refs]
    idx_count = sum(1 for i in items if isinstance(i, IndexPage))
    print(f"   {len(items)} عنصر ({idx_count} فهارس تلقائية)\n")

    print("4) بناء EPUB…")
    export_epub(items)

    print("5) بناء Markdown…")
    export_markdown(items)

    print("\n✓ اكتمل")


if __name__ == "__main__":
    main()
