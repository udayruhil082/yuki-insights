"""Competitor Radar collector.

Pulls the latest public news and YouTube uploads for every brand in
config/brands.json, tags each item (Launch, Campaign, Influencer...),
merges it into docs/data/items.json and rebuilds the stats, the weekly
digest and the Excel export. Uses only free, public sources.

Run:  python radar/collect.py
"""
import hashlib
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config" / "brands.json").read_text())
DATA = ROOT / "docs" / "data"
ITEMS_FILE = DATA / "items.json"
KEEP_DAYS = 400
UA = "Mozilla/5.0 (compatible; CompetitorRadar/1.0)"
NOW = datetime.now(timezone.utc)

# Tag -> keyword patterns (matched on title + summary, case-insensitive)
TAGS = {
    "Launch": r"\b(launch\w*|unveil\w*|introduc\w*|rolls? out|debut\w*|new range|new variant|forays?)\b",
    "Campaign": r"\b(campaign\w*|ad film|brand film|tvc|commercial|ad\b|ads\b|advert\w*|#\w+)",
    "Influencer": r"\b(influencer\w*|creator\w*|instagram|reels?|ugc|collab\w*|youtuber)\b",
    "Celebrity": r"\b(ambassador\w*|signs|roped in|ropes in|actor|actress|cricketer|face of)\b",
    "Sustainability": r"\b(sustainab\w*|plastic|recycl\w*|vegan|cruelty|planet|eco\w*|refill\w*)\b",
    "Pricing & Promo": r"\b(price\w*|discount\w*|offer\w*|sale\b|cheaper|pricing|₹|rs\.?\s?\d)",
    "Business": r"\b(revenue|market share|quarter\w*|q[1-4]\b|profit\w*|growth|volume|sales)\b",
    "Partnership": r"\b(partner\w*|tie-?up|blinkit|zepto|instamart|nykaa|amazon|flipkart|quick commerce|q-?comm)\b",
    "Purpose": r"\b(real beauty|self-esteem|women|ageism|inclusiv\w*|mental health|confidence)\b",
}
BIG_MOVE_TAGS = {"Launch", "Campaign", "Celebrity", "Partnership"}


def log(*a):
    print(*a, file=sys.stderr)


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-IN,en"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def strip_html(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def tag_text(text):
    return [t for t, pat in TAGS.items() if re.search(pat, text, re.I)]


def item_id(url, title):
    return hashlib.sha1((title.lower().strip() + "|" + url).encode()).hexdigest()[:16]


def parse_date(s):
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return NOW.isoformat()
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat()


# ---------- Sources ----------

def source_type(publisher, domain, brand=None):
    """Official (brand / parent company), Trade press (marketing news) or News."""
    d = (domain or "").lower()
    b = next((x for x in CONFIG["brands"] if x["name"] == brand), {})
    if any(o in d for o in b.get("official_sites", [])):
        return "Official"
    if any(t in d for t in CONFIG.get("trade_press", [])) or re.search(
            r"afaqs|exchange4media|brandequity|campaign india|bestmediainfo|medianews4u|adgully|social samosa|storyboard18|indiantelevision", publisher or "", re.I):
        return "Trade press"
    return "News"


# Headlines left out on purpose:
#  - agency appointments and anything naming an agency (team rule: no agency names)
#  - stock-market and quarterly-results chatter (kept if it is about ad spend)
#  - shop blog / SEO articles and empty titles
SKIP_TITLE = re.compile(r"\b(agency|agencies|AOR|account win|wins .{0,40} account|creative mandate|media mandate|"
                        r"share price|stock|shares|target price|rated (buy|sell|hold)|investor|dividend|Q[1-4] results|quarterly results|"
                        r"seasonal returns|analyst)\b", re.I)
AGENCY_NAMES = re.compile(r"\b(mindshare|hogarth|omnicom|wpp|ogilvy|lowe lintas|mullenlowe|dentsu|publicis|leo burnett|mccann|"
                          r"havas|fcb|ddb|tbwa|wavemaker|groupm|madison media|anymind|digitas|schbang|talented agency|"
                          r"social panga|kinnect|bbh|vml|lodestar|mediacom|essencemediacom|starcom|spark foundry|omd|carat|"
                          r"iprospect|wunderman|white rivers|infectious|moonshine|tilt brand|the glitch|famous innovations|"
                          r"grey group|grey india|initiative india|zenith india|phd india)\b", re.I)
FINANCE = re.compile(r"net profit|profit (rises|jumps|falls|surges|up|down|grows|soars)|revenue (rises|jumps|up|grows|soars)|"
                     r"net sales|earnings|\bq[1-4]\s?fy\d*|quarterly profit|\bq[1-4] (net|profit|revenue)|upside|maintains|"
                     r"buy rating|tax demand|webcast|(third|3rd|second|2nd|first|1st|fourth|4th)[- ]quarter|cuts guidance|"
                     r"results:|turnaround|arbitration", re.I)
AD_MONEY = re.compile(r"ad spend|advertis|marketing spend|a&p", re.I)
BLOG = re.compile(r"^\s*-\s|^how to use|step-by-step guide|reddit|what the reviews|debate:|^\s*$", re.I)
SKIP_PUBLISHER = re.compile(r"yahoo finance|marketsmojo|simply wall|scanx|moneycontrol|marketscreener|investing\.com|"
                            r"tickertape|trendlyne|equitymaster|stock|marketbeat|tradingview", re.I)
# News about products outside body wash / soap / scrub is marked so the app can hide it.
OTHER_CAT = re.compile(r"\b(hair\w*|shampoo|conditioner|sunscreen|spf|lip\w*|face ?wash|makeup|fragrance|perfume|"
                       r"underarm|deo\w*|toothpaste|oral|innerwear|lotion|moistur\w*|serum|mist|cream|creme|grooming)\b", re.I)
OUR_CAT = re.compile(r"body ?wash|shower|soap|bath\w*|scrub|\bbars?\b|exfoliat\w*|personal wash|skin ?care", re.I)


# Team rule: no parent company names in the app.
PARENT = re.compile(r"\b(ITC|HUL|Hindustan Unilever( Limited)?|Unilever|Wipro( Consumer Care)?|Colgate[- ]Palmolive( India)?|"
                    r"Beiersdorf|Honasa( Consumer)?|L['’]Or[eé]al( India)?|Innovist)\b", re.I)
BRAND_NAMES = "|".join(re.escape(b["name"]) for b in CONFIG["brands"])
PARENT_PREFIX = re.compile(r"\b(ITC|HUL['’]s|Unilever['’]s|Wipro['’]s)\s+(?=(" + BRAND_NAMES + r")\b)", re.I)


def hide_parent(i):
    """Removes parent company names. Returns False if the headline only makes sense with them (then it is left out)."""
    if PARENT.search(i.get("publisher", "")):
        i["publisher"] = "Brand website" if i.get("source_type") == "Official" else "Company website"
    i["title"] = PARENT_PREFIX.sub("", i["title"])       # "ITC Fiama ..." -> "Fiama ..."
    if i.get("summary"):
        i["summary"] = re.sub(r"\s{2,}", " ", PARENT.sub("", re.sub(r"#\w*(ITC|HUL)\w*", "", i["summary"]))).strip()
    return not PARENT.search(i["title"])


def skip_news(title, publisher="", domain="", brand=None):
    """True if a news headline should be left out."""
    if BLOG.search(title) or SKIP_TITLE.search(title) or AGENCY_NAMES.search(title):
        return True
    if FINANCE.search(title) and not AD_MONEY.search(title):
        return True
    if SKIP_PUBLISHER.search(f"{publisher} {domain}"):
        return True
    b = next((x for x in CONFIG["brands"] if x["name"] == brand), {})
    if b.get("must_regex") and not re.search(b["must_regex"], title, re.I):
        return True
    if b.get("exclude_regex") and re.search(b["exclude_regex"], title, re.I):
        return True
    return False


def mentions(title, words):
    """Whole-word match, so 'Lux' does not match 'luxury'."""
    return any(re.search(r"\b" + re.escape(w) + r"\b", title, re.I) for w in words)


FOREIGN = re.compile(r"\b(U\.?S\.?A?|UK|U\.K\.|America\w*|Europe\w*|Australia\w*|Canada|Germany|Japan|China|Philippines|"
                     r"Indonesia|Malaysia|Thailand|Brazil|Mexico|Africa\w*|Pakistan|Bangladesh|Sri Lanka|Nepal|global(ly)?|worldwide)\b")
AD_LAUNCH = re.compile(r"campaign|\btvc\b|\bad\b|ad film|\bfilm\b|ambassador|collaboration|collab|\bhub\b|lollapalooza|story|stories", re.I)
INDIA = re.compile(r"\bindia\w*\b|₹|\brs\.?\s?\d|crore|lakh", re.I)


def launch_check(i):
    """How sure we are that a launch is real, in India and in our categories."""
    if i["kind"] == "product":
        return "shop"                      # seen on the brand's own online shop
    if i["kind"] in ("internal", "manual"):
        return "team"
    text = i["title"] + " " + (i.get("summary") or "")[:300]
    in_india = (bool(INDIA.search(text)) or i.get("source_type") in ("Trade press", "Official")
                or (i.get("source_domain") or "").endswith(".in")) and not FOREIGN.search(i["title"])
    about_ads = AD_LAUNCH.search(i["title"])   # "launches a campaign / TVC" is a campaign, not a product launch
    if not (in_india and not other_category(text) and not about_ads):
        return None
    return "brand" if i.get("source_type") == "Official" else "news"   # brand's own site or YouTube channel


def other_category(text):
    return bool(OTHER_CAT.search(text)) and not OUR_CAT.search(text)


def google_news(query, brand, must=None, days=30):
    r = CONFIG["region"]
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query + f" when:{days}d", "hl": r["hl"], "gl": r["gl"], "ceid": r["ceid"]})
    root = ET.fromstring(fetch(url))
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = it.find("source")
        publisher = src.text.strip() if src is not None and src.text else ""
        domain = urllib.parse.urlparse(src.get("url", "")).netloc if src is not None else ""
        if publisher and title.endswith(" - " + publisher):
            title = title[: -len(publisher) - 3]
        if must and not mentions(title, must):
            continue
        if skip_news(title, publisher, domain, brand):
            continue
        link = it.findtext("link") or ""
        summary = strip_html(it.findtext("description"))
        if summary.startswith(title):
            summary = ""
        out.append({
            "brand": brand, "kind": "news", "title": title, "url": link,
            "publisher": publisher, "published": parse_date(it.findtext("pubDate") or ""),
            "summary": summary[:400], "source_domain": domain,
            "source_type": source_type(publisher, domain, brand),
        })
    return out


def youtube_channel_id(handle):
    if re.fullmatch(r"UC[\w-]{22}", handle):   # a channel id was given directly
        return handle
    page = fetch("https://www.youtube.com/" + handle.lstrip("/")).decode("utf-8", "ignore")
    m = re.search(r'"(?:externalId|channelId)":"(UC[\w-]{22})"', page) or \
        re.search(r'channel/(UC[\w-]{22})', page)
    return m.group(1) if m else None


YT_NAMES = {}


def youtube(handle, brand, cache):
    cid = cache.get(handle) or youtube_channel_id(handle)
    if not cid:
        raise RuntimeError(f"could not find channel id for {handle}")
    cache[handle] = cid
    ns = {"a": "http://www.w3.org/2005/Atom", "m": "http://search.yahoo.com/mrss/",
          "yt": "http://www.youtube.com/xml/schemas/2015"}
    root = ET.fromstring(fetch(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"))
    YT_NAMES[brand] = root.findtext("a:title", namespaces=ns) or ""   # shown in Sources so the channel can be checked
    if brand.split()[0].lower() not in YT_NAMES[brand].lower().replace(" ", ""):
        raise RuntimeError(f"channel is '{YT_NAMES[brand]}', which does not look like {brand}; skipped")
    out = []
    for e in root.findall("a:entry", ns):
        vid = e.findtext("yt:videoId", namespaces=ns)
        stats = e.find("m:group/m:community/m:statistics", ns)
        views = int(stats.get("views")) if stats is not None and stats.get("views") else None
        desc = e.findtext("m:group/m:description", namespaces=ns) or ""
        out.append({
            "brand": brand, "kind": "youtube",
            "title": e.findtext("a:title", namespaces=ns) or "",
            "url": f"https://www.youtube.com/watch?v={vid}",
            "publisher": "YouTube", "published": parse_date(e.findtext("a:published", namespaces=ns) or ""),
            "summary": desc[:400], "views": views, "source_type": "Official",
            "thumb": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
        })
    return out


def local_logs():
    """Posts logged with the in-app 'Log a post' form (desktop app)."""
    f = DATA / "logged_posts.json"
    if not f.exists():
        return []
    out = []
    for p in json.loads(f.read_text()):
        out.append({
            "brand": p.get("brand") or "Other", "kind": "manual", "title": p.get("what") or "Logged post",
            "url": p.get("link") or "", "publisher": p.get("platform") or "Logged",
            "published": parse_date(p.get("date") or p.get("logged_at") or ""),
            "summary": p.get("notes", ""), "creator": p.get("creator", ""), "metrics": p.get("metrics", ""),
            "source_type": "Logged by team",
        })
    return out


def manual_logs():
    """Posts logged through the 'Log a competitor post' GitHub issue form, or the desktop app form."""
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        return local_logs()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues?labels=post-log&state=all&per_page=100",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": UA})
    issues = json.loads(urllib.request.urlopen(req, timeout=25).read())
    out = []
    for iss in issues:
        fields = dict(re.findall(r"### (.+?)\n\n(.+?)(?=\n\n###|\Z)", iss.get("body") or "", re.S))
        f = {k.strip(): v.strip() for k, v in fields.items()}
        def g(k):
            v = f.get(k, "")
            return "" if v == "_No response_" else v
        link = g("Link to the post")
        out.append({
            "brand": g("Brand") or "Other", "kind": "manual",
            "title": g("What happened (one line)") or iss["title"],
            "url": link or iss["html_url"], "publisher": g("Platform") or "Logged",
            "published": parse_date(g("Date posted") or iss["created_at"]),
            "summary": g("Notes / why it matters"), "creator": g("Creator handle (if any)"),
            "metrics": g("Likes / comments / views (if visible)"),
            "logged_by": iss["user"]["login"], "source_type": "Logged by team",
        })
    return out


# ---------- Build outputs ----------

def merge(existing, new):
    by_id = {i["id"]: i for i in existing}
    added = 0
    for it in new:
        it["id"] = item_id(it["url"], it["title"])
        it["tags"] = it.get("tags") or tag_text(it["title"] + " " + it.get("summary", ""))
        if it["id"] in by_id:
            old = by_id[it["id"]]
            if it.get("views"):
                old["views"] = it["views"]
            if it["kind"] == "manual":
                old.update(it)
            continue
        # also drop near-duplicate headlines across publishers (same brand, same title)
        key = (it["brand"], re.sub(r"\W+", "", it["title"].lower())[:60])
        if any((o["brand"], re.sub(r"\W+", "", o["title"].lower())[:60]) == key for o in by_id.values()):
            continue
        it["first_seen"] = NOW.isoformat()
        by_id[it["id"]] = it
        added += 1
    cutoff = (NOW - timedelta(days=KEEP_DAYS)).isoformat()
    items = [i for i in by_id.values() if i["published"] >= cutoff
             and not (i["kind"] == "news" and skip_news(i["title"], i.get("publisher", ""), i.get("source_domain", ""), i["brand"]))
             and not (i["kind"] == "youtube" and AGENCY_NAMES.search(i["title"]))]
    items = [i for i in items if i["kind"] in ("internal", "manual") or hide_parent(i)]
    for i in items:   # re-checked every run so rule changes apply to old items too
        i["tags"] = tag_text(i["title"] + " " + i.get("summary", "")) if i["kind"] in ("news", "youtube") else i["tags"]
        if "Launch" in i["tags"]:
            i["launch"] = launch_check(i)
            if not i["launch"]:            # not clearly an India soap / body wash / scrub launch
                i["tags"].remove("Launch")
        else:
            i.pop("launch", None)
        i["other_category"] = i["kind"] in ("news", "youtube") and other_category(i["title"] + " " + (i.get("summary") or "")[:200])
    items.sort(key=lambda i: i["published"], reverse=True)
    return items, added


def week_key(iso):
    d = datetime.fromisoformat(iso)
    monday = (d - timedelta(days=d.weekday())).date()
    return monday.isoformat()


def build_stats(items, run_log):
    brands = [b["name"] for b in CONFIG["brands"]]
    weeks = sorted({week_key(i["published"]) for i in items})[-12:]
    sov = {w: Counter() for w in weeks}
    tags = defaultdict(Counter)
    for i in items:
        w = week_key(i["published"])
        if w in sov:
            sov[w][i["brand"]] += 1
        if i["published"] >= (NOW - timedelta(days=90)).isoformat():
            for t in i["tags"]:
                tags[i["brand"]][t] += 1
    return {
        "updated": NOW.isoformat(),
        "brands": [{k: b[k] for k in ("name", "role", "instagram", "ad_library_query", "youtube_handle", "website", "official_sites") if k in b}
                   for b in CONFIG["brands"]],
        "weeks": weeks,
        "share_of_voice": {w: {b: sov[w][b] for b in brands} for w in weeks},
        "tags_90d": {b: dict(tags[b]) for b in brands},
        "tag_names": list(TAGS),
        "big_move_tags": sorted(BIG_MOVE_TAGS),
        "run_log": run_log,
    }


def build_digest(items):
    since = (NOW - timedelta(days=7)).isoformat()
    recent = [i for i in items if i["published"] >= since]
    lines = [f"# Yuki Insights weekly digest ({(NOW - timedelta(days=7)):%d %b} to {NOW:%d %b %Y})", ""]
    counts = Counter(i["brand"] for i in recent)
    lines.append("**Mentions this week:** " + ", ".join(f"{b} {n}" for b, n in counts.most_common()) or "none")
    lines.append("")
    for b in [x["name"] for x in CONFIG["brands"]]:
        bi = [i for i in recent if i["brand"] == b]
        if not bi:
            continue
        big = [i for i in bi if BIG_MOVE_TAGS & set(i["tags"])]
        lines.append(f"## {b} ({len(bi)} items{', ' + str(len(big)) + ' big moves' if big else ''})")
        for i in (big + [x for x in bi if x not in big])[:6]:
            tags = f" `{', '.join(i['tags'])}`" if i["tags"] else ""
            lines.append(f"- [{i['title']}]({i['url']}) ({i['publisher']}){tags}")
        lines.append("")
    lines.append("Open the dashboard for everything, and add your 'what Fiama should do' notes there.")
    return "\n".join(lines)


def build_excel(items, path):
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        log("openpyxl not installed, skipping Excel export")
        return
    seed = json.loads((DATA / "seed.json").read_text()) if (DATA / "seed.json").exists() else {"sheets": {}}
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Live Feed"
    cols = ["Date", "Brand", "Type", "Source type", "Title", "Publisher", "Tags", "Views", "Link", "Summary / notes"]
    ws.append(cols)
    for i in items:
        ws.append([i["published"][:10], i["brand"], i["kind"], i.get("source_type", ""), i["title"], i["publisher"],
                   ", ".join(i["tags"]), i.get("views"), i["url"], i.get("summary", "")])
    pf = DATA / "products.json"
    if pf.exists():
        ps = wb.create_sheet("Product Range (live)")
        ps.append(["Brand", "Category", "Product", "Size / variant", "Price (INR)", "MRP (INR)", "In stock", "Source", "First seen", "Link"])
        for p in json.loads(pf.read_text())["products"]:
            ps.append([p["brand"], p["category"], p["product"], p["size"], p["price"], p["mrp"],
                       {True: "Yes", False: "No"}.get(p["in_stock"], ""), p["source"], p.get("first_seen", "")[:10], p["url"]])
    for name, sheet in seed["sheets"].items():
        s = wb.create_sheet(name[:31])
        s.append(sheet["columns"])
        for r in sheet["rows"]:
            s.append([r.get(c, "") for c in sheet["columns"]])
    for s in wb.worksheets:
        for c in s[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="8E3B6E")
        s.freeze_panes = "A2"
        for col in s.columns:
            s.column_dimensions[col[0].column_letter].width = min(
                60, max(10, max(len(str(c.value or "")) for c in col[:50]) + 2))
    wb.save(path)


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    existing = json.loads(ITEMS_FILE.read_text()) if ITEMS_FILE.exists() else []
    cache_file = DATA / "youtube_ids.json"
    yt_cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    new, run_log = [], []

    def attempt(label, fn):
        try:
            got = fn()
            new.extend(got)
            run_log.append({"source": label, "ok": True, "found": len(got)})
            log(f"OK   {label}: {len(got)}")
        except Exception as e:
            run_log.append({"source": label, "ok": False, "error": str(e)[:200]})
            log(f"FAIL {label}: {e}")

    for b in CONFIG["brands"]:
        must = b.get("must_mention")
        attempt(f"News: {b['name']}", lambda b=b, must=must: google_news(b["news_query"], b["name"], must))
        if CONFIG.get("trade_press"):
            q = f'"{b["name"]}" (' + " OR ".join("site:" + d for d in CONFIG["trade_press"]) + ")"
            attempt(f"Trade press: {b['name']}", lambda q=q, b=b, must=must: google_news(q, b["name"], must, 365))
        if b.get("official_sites"):
            q = f'"{b["name"]}" (' + " OR ".join("site:" + d for d in b["official_sites"]) + ")"
            attempt(f"Official: {b['name']}", lambda q=q, b=b, must=must: google_news(q, b["name"], must, 365))
        if b.get("youtube_handle"):
            attempt(f"YouTube: {b['name']}", lambda b=b: youtube(b["youtube_handle"], b["name"], yt_cache))
    cw = CONFIG.get("category_watch")
    if cw:
        attempt("News: Category", lambda: google_news(cw["news_query"], "Category"))
    attempt("Logged posts", manual_logs)
    for r in run_log:
        if r["source"].startswith("YouTube: ") and r["source"][9:] in YT_NAMES:
            r["channel"] = YT_NAMES[r["source"][9:]]

    # Product range: new products, price changes, stock (see radar/products.py)
    try:
        import products
        changes, plog = products.run(log)
        new.extend(changes)
        run_log.extend(plog)
    except Exception as e:
        run_log.append({"source": "Products", "ok": False, "error": str(e)[:200]})
        log(f"FAIL Products: {e}")

    items, added = merge(existing, new)
    ITEMS_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=0))
    cache_file.write_text(json.dumps(yt_cache, indent=1))
    (DATA / "stats.json").write_text(json.dumps(build_stats(items, run_log), ensure_ascii=False, indent=1))
    digest = build_digest(items)
    (DATA / "digest.md").write_text(digest)
    build_excel(items, DATA / "competitor_radar.xlsx")
    log(f"Done: {added} new items, {len(items)} total")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"added={added}\n")


if __name__ == "__main__":
    main()
