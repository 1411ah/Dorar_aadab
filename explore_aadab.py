#!/usr/bin/env python3
"""
explore_aadab.py — استكشاف بنية dorar.net/aadab قبل بناء سكريبت التصدير
Usage:
    python explore_aadab.py
    SAMPLE=20 python explore_aadab.py   # استكشاف أول 20 صفحة فقط
Output:
    exploration_report.txt  — تقرير مفصّل
    sampled_pages.json      — بيانات الصفحات المُستكشَفة
"""

import json
import os
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ── إعدادات ───────────────────────────────────────────────────────────────────
START_URL  = "https://dorar.net/aadab"
BASE_URL   = "https://dorar.net"
PAGE_RE    = re.compile(r"/aadab/(\d+)")
DELAY      = 0.4
TIMEOUT    = 20
SAMPLE     = int(os.getenv("SAMPLE") or 30)   # 0 = اكتشاف كامل

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
}

session = requests.Session()
session.headers.update(HEADERS)


# ── جلب HTML ──────────────────────────────────────────────────────────────────
def fetch(url: str) -> BeautifulSoup | None:
    try:
        r = session.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        r.encoding = "utf-8"
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print(f"  [ERROR] {url}: {e}")
        return None


# ── اكتشاف كل الروابط من صفحة الفهرس ─────────────────────────────────────────
def discover_all_ids() -> list[int]:
    print(f"[1] جلب صفحة الفهرس: {START_URL}")
    soup = fetch(START_URL)
    if not soup:
        return []
    ids = set()
    for a in soup.find_all("a", href=True):
        m = PAGE_RE.search(a["href"])
        if m:
            ids.add(int(m.group(1)))
    ids_sorted = sorted(ids)
    print(f"    عدد المعرّفات المكتشفة: {len(ids_sorted)}")
    print(f"    أصغر معرّف: {ids_sorted[0]}   أكبر معرّف: {ids_sorted[-1]}")
    return ids_sorted


# ── تحليل صفحة واحدة ─────────────────────────────────────────────────────────
def analyze_page(url: str, pid: int) -> dict:
    soup = fetch(url)
    if not soup:
        return {"pid": pid, "url": url, "error": True}

    info = {
        "pid":  pid,
        "url":  url,
        "error": False,
    }

    # ── العنوان ──
    h1 = soup.find("h1", class_="h5-responsive")
    info["title_h1"]    = h1.get_text(strip=True) if h1 else None
    og = soup.find("meta", property="og:title")
    info["title_og"]    = og["content"].split(" - ")[0].strip() if og else None
    t  = soup.find("title")
    info["title_tag"]   = t.get_text().split(" - ")[0].strip() if t else None

    # ── Breadcrumb ──
    bc = soup.find("ol", class_="breadcrumb")
    if bc:
        crumbs = [li.get_text(strip=True) for li in bc.find_all("li") if li.get_text(strip=True)]
        info["breadcrumb"] = crumbs
        info["bc_depth"]   = len(crumbs)
    else:
        info["breadcrumb"] = []
        info["bc_depth"]   = 0

    # ── حاوية المحتوى الرئيسية ──
    cntnt = soup.find("div", id="cntnt")
    info["has_cntnt_div"] = cntnt is not None

    if cntnt:
        # البحث عن div.w-100.mt-4 (النمط المعروف من الكود القديم)
        w100 = cntnt.find("div", class_=lambda c: c and "w-100" in c and "mt-4" in c)
        info["has_w100_mt4"] = w100 is not None

        # أنواع العناوين داخل المحتوى
        heading_tags = [t.name for t in cntnt.find_all(["h1","h2","h3","h4","h5","h6"])]
        info["headings_in_content"] = Counter(heading_tags)

        # span classes المستخدمة
        span_classes = []
        for sp in cntnt.find_all("span", class_=True):
            span_classes.extend(sp["class"])
        info["span_classes"] = Counter(span_classes)

        # هل يوجد هوامش (tip spans)؟
        info["footnote_tips"] = len(cntnt.find_all("span", class_="tip"))

        # حجم المحتوى (عدد الكلمات تقريباً)
        text = cntnt.get_text(separator=" ", strip=True)
        info["word_count"] = len(text.split())

        # أول 200 حرف من النص
        info["text_preview"] = text[:200]

    # ── رابط "التالي" ──
    next_url = None
    for a in soup.find_all("a", href=True):
        if a.get_text(strip=True) == "التالي":
            m = PAGE_RE.search(a["href"])
            if m:
                next_url = urljoin(BASE_URL, a["href"])
                break
    info["next_url"]  = next_url
    info["next_pid"]  = int(PAGE_RE.search(next_url).group(1)) if next_url else None

    # ── هل هناك صفحة سابقة؟ ──
    prev_url = None
    for a in soup.find_all("a", href=True):
        if a.get_text(strip=True) == "السابق":
            m = PAGE_RE.search(a["href"])
            if m:
                prev_url = urljoin(BASE_URL, a["href"])
                break
    info["prev_url"] = prev_url
    info["prev_pid"] = int(PAGE_RE.search(prev_url).group(1)) if prev_url else None

    return info


# ── تتبع السلسلة المتسلسلة ────────────────────────────────────────────────────
def follow_chain(start_pid: int, limit: int) -> list[dict]:
    """
    تتبع سلسلة التالي/السابق بدءاً من أصغر معرّف.
    يكشف الترتيب الحقيقي للصفحات.
    """
    results = []
    url     = f"{BASE_URL}/aadab/{start_pid}"
    visited = set()

    while url and (limit == 0 or len(results) < limit):
        if url in visited:
            break
        visited.add(url)
        m = PAGE_RE.search(url)
        pid = int(m.group(1)) if m else 0
        print(f"  [{len(results)+1:>4}] pid={pid}  {url}")
        info = analyze_page(url, pid)
        results.append(info)
        time.sleep(DELAY)
        url = info.get("next_url")

    return results


# ── فحص الصفحات الخاصة المحتملة ─────────────────────────────────────────────
def probe_special_pages() -> dict:
    special = {
        "index":    START_URL,
        "refs":     "https://dorar.net/refs/aadab",
        "article":  "https://dorar.net/article/2112",   # منهج العمل
    }
    results = {}
    print("\n[3] فحص الصفحات الخاصة…")
    for name, url in special.items():
        print(f"  → {name}: {url}")
        soup = fetch(url)
        if not soup:
            results[name] = {"url": url, "error": True}
            continue

        cntnt = soup.find("div", id="cntnt")
        info  = {
            "url":            url,
            "has_cntnt":      cntnt is not None,
            "main_divs":      [],
            "article_count":  0,
            "text_preview":   "",
        }
        if cntnt:
            for div in cntnt.find_all("div", recursive=False):
                cls = " ".join(div.get("class", []))
                info["main_divs"].append(cls or "(no-class)")
            info["article_count"] = len(cntnt.find_all("article"))
            info["text_preview"]  = cntnt.get_text(separator=" ", strip=True)[:300]
        results[name] = info
        time.sleep(DELAY)
    return results


# ── إحصاءات مجمّعة ───────────────────────────────────────────────────────────
def aggregate_stats(pages: list[dict]) -> dict:
    stats = {
        "total_sampled":      len(pages),
        "errors":             sum(1 for p in pages if p.get("error")),
        "bc_depth_dist":      Counter(p.get("bc_depth", 0) for p in pages if not p.get("error")),
        "has_w100_mt4":       sum(1 for p in pages if p.get("has_w100_mt4")),
        "has_footnotes":      sum(1 for p in pages if (p.get("footnote_tips") or 0) > 0),
        "all_span_classes":   Counter(),
        "all_heading_types":  Counter(),
        "max_word_count":     0,
        "avg_word_count":     0,
        "next_chain_gaps":    [],    # فجوات في سلسلة التالي
    }

    word_counts = []
    for p in pages:
        if p.get("error"):
            continue
        for cls, cnt in (p.get("span_classes") or {}).items():
            stats["all_span_classes"][cls] += cnt
        for tag, cnt in (p.get("headings_in_content") or {}).items():
            stats["all_heading_types"][tag] += cnt
        wc = p.get("word_count", 0)
        word_counts.append(wc)
        if wc > stats["max_word_count"]:
            stats["max_word_count"] = wc

    if word_counts:
        stats["avg_word_count"] = round(sum(word_counts) / len(word_counts))

    # فجوات في السلسلة المتسلسلة
    for i in range(len(pages) - 1):
        cur  = pages[i]
        nxt  = pages[i + 1]
        if cur.get("next_pid") and nxt.get("pid"):
            if cur["next_pid"] != nxt["pid"]:
                stats["next_chain_gaps"].append({
                    "from_pid": cur["pid"],
                    "expected": cur["next_pid"],
                    "actual":   nxt["pid"],
                })

    return stats


# ── كتابة التقرير ─────────────────────────────────────────────────────────────
def write_report(ids: list[int], pages: list[dict], stats: dict, special: dict) -> None:
    lines = []
    add   = lines.append

    add("=" * 70)
    add("  تقرير استكشاف موسوعة الآداب الشرعية — dorar.net/aadab")
    add("=" * 70)

    add(f"\n── معرّفات الصفحات ──────────────────────────────────────────")
    add(f"  عدد المعرّفات المكتشفة في الفهرس : {len(ids)}")
    if ids:
        add(f"  المدى                            : {ids[0]} → {ids[-1]}")
        gaps = [ids[i+1]-ids[i] for i in range(len(ids)-1) if ids[i+1]-ids[i] > 1]
        add(f"  عدد الفجوات في الأرقام           : {len(gaps)}")
        if gaps:
            add(f"  أمثلة على الفجوات                : {gaps[:10]}")

    add(f"\n── السلسلة المتسلسلة (التالي/السابق) ───────────────────────")
    add(f"  صفحات تم استكشافها               : {stats['total_sampled']}")
    add(f"  أخطاء                            : {stats['errors']}")
    add(f"  توزيع عمق breadcrumb             : {dict(sorted(stats['bc_depth_dist'].items()))}")
    add(f"  صفحات تحتوي div.w-100.mt-4       : {stats['has_w100_mt4']}")
    add(f"  صفحات بها هوامش (tip spans)      : {stats['has_footnotes']}")
    add(f"  متوسط عدد الكلمات               : {stats['avg_word_count']}")
    add(f"  أكبر عدد كلمات في صفحة          : {stats['max_word_count']}")

    if stats["next_chain_gaps"]:
        add(f"\n  ⚠ فجوات في السلسلة:")
        for g in stats["next_chain_gaps"][:5]:
            add(f"    pid={g['from_pid']} → expected={g['expected']} actual={g['actual']}")
    else:
        add(f"\n  ✓ السلسلة متصلة بلا فجوات في العيّنة")

    add(f"\n── أنواع span classes في المحتوى ────────────────────────────")
    for cls, cnt in stats["all_span_classes"].most_common(20):
        add(f"  {cls:<25} : {cnt}")

    add(f"\n── أنواع العناوين في المحتوى ────────────────────────────────")
    for tag, cnt in sorted(stats["all_heading_types"].items()):
        add(f"  <{tag}>  : {cnt}")

    add(f"\n── نماذج من الصفحات المُستكشَفة ─────────────────────────────")
    for p in pages[:5]:
        if p.get("error"):
            add(f"\n  pid={p['pid']} [ERROR]")
            continue
        add(f"\n  pid={p['pid']}  url={p['url']}")
        add(f"    title_h1   : {p.get('title_h1')}")
        add(f"    breadcrumb : {' > '.join(p.get('breadcrumb', []))}")
        add(f"    bc_depth   : {p.get('bc_depth')}  |  words: {p.get('word_count')}")
        add(f"    spans      : {dict(p.get('span_classes', {}))}")
        add(f"    preview    : {p.get('text_preview', '')[:120]}")

    add(f"\n── الصفحات الخاصة ───────────────────────────────────────────")
    for name, info in special.items():
        add(f"\n  [{name}] {info.get('url')}")
        if info.get("error"):
            add(f"    ERROR")
            continue
        add(f"    has_cntnt    : {info.get('has_cntnt')}")
        add(f"    main_divs    : {info.get('main_divs', [])[:5]}")
        add(f"    articles     : {info.get('article_count')}")
        add(f"    preview      : {info.get('text_preview', '')[:200]}")

    add("\n" + "=" * 70)

    report_text = "\n".join(lines)
    print("\n" + report_text)
    Path("exploration_report.txt").write_text(report_text, encoding="utf-8")
    print(f"\n  → تقرير محفوظ في: exploration_report.txt")


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    mode = f"SAMPLE ({SAMPLE})" if SAMPLE else "FULL"
    print(f"=== استكشاف dorar.net/aadab  [{mode}] ===\n")

    # 1. اكتشاف كل المعرّفات من الفهرس
    all_ids = discover_all_ids()

    # 2. تتبع السلسلة المتسلسلة بدءاً من أصغر معرّف
    print(f"\n[2] تتبع السلسلة المتسلسلة (حد={SAMPLE or 'بلا حد'})…")
    sampled = follow_chain(
        start_pid = all_ids[0] if all_ids else 2,
        limit     = SAMPLE,
    )

    # 3. فحص الصفحات الخاصة
    special = probe_special_pages()

    # 4. إحصاءات مجمّعة
    stats = aggregate_stats(sampled)

    # 5. كتابة التقرير
    write_report(all_ids, sampled, stats, special)

    # 6. حفظ JSON
    output = {
        "all_ids":   all_ids,
        "sampled":   sampled,
        "stats":     {
            k: dict(v) if isinstance(v, Counter) else v
            for k, v in stats.items()
        },
        "special":   special,
    }
    Path("sampled_pages.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"  → بيانات JSON محفوظة في: sampled_pages.json")
    print("\n✓ اكتمل الاستكشاف")


if __name__ == "__main__":
    main()
