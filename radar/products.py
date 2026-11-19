"""Product range checker.

For each brand in config/brands.json, reads its public product catalogue and
compares it with the last check, so the dashboard can show new products,
price changes, out-of-stock items and removed products.

  shopify : the brand's own online store publishes /products.json
            (names, sizes, prices, stock). Mamaearth, Chemist at Play (innovist.com),
            Love Beauty and Planet, Ghar Soaps.
  sitemap : the brand website's public page list. Spots new or removed
            product pages only (no prices).

Called by collect.py; can also be run alone:  python radar/products.py
"""
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config" / "brands.json").read_text())
DATA = ROOT / "docs" / "data"
FILE = DATA / "products.json"
UA = "Mozilla/5.0 (compatible; CompetitorRadar/1.0)"
NOW = datetime.now(timezone.utc).isoformat()


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# Words that mean the item is not a single body wash, soap or body scrub.
NOT_BODY = re.compile(r"\b(face|facial|lip|scalp|underarm|hair|shampoo|conditioner|toner|peel(ing)?|serum solution|"
                      r"hand ?wash|dish ?wash|face ?wash|lotion(?! soap)|moisturi[sz]er|roll ?on|mist|deo|cream(?! beauty bar)|"
                      r"cr[eè]me|shower cap|scrub-a-dub)\b", re.I)
# Bundles that mix different products are left out (packs of the same item are kept).
BUNDLE = re.compile(r"\b(kit|combo|duo|trio|set|gift|essentials)\b|"
                    r"(\d\s*(ml|gm|g|l)\b|\))\s*\+[^|]*?\b(wash|soap|scrub|lotion|shampoo|roll ?on|bar|cream|loofah)", re.I)


def category_of(title):
    """Category from the product NAME only (shop tags are too loose)."""
    t = title.lower()
    if re.search(r"body ?wash|shower gel|shower ?wash|head to toe wash", t):
        return "Body wash / shower gel"
    if re.search(r"\bscrub\b", t):
        return "Body scrub"
    if re.search(r"\bsoap\b|bathing bar|gel bar|beauty bar|serum bar|milk bar|\bbar\b", t):
        return "Soap / gel bar"
    return None


def classify(title, product_type=""):
    """Returns the category, or None if the item should be left out."""
    # decided from the product name only: shop "product type" labels proved unreliable
    # (they hid Fiama's De-Tan Body Scrub)
    if NOT_BODY.search(title) or BUNDLE.search(title):
        return None
    return category_of(title)


def shopify(brand, base, vendor=None):
    out, page = [], 1
    while page <= 10:
        data = json.loads(fetch(f"{base.rstrip('/')}/products.json?limit=250&page={page}"))
        prods = data.get("products", [])
        if not prods:
            break
        for p in prods:
            if vendor and vendor.lower() not in (p.get("vendor") or "").lower() + " " + (p.get("title") or "").lower():
                continue
            text = " ".join([p.get("title", ""), p.get("product_type", ""), " ".join(p.get("tags", []) if isinstance(p.get("tags"), list) else [str(p.get("tags", ""))])])
            cat = classify(p.get("title", ""), p.get("product_type", ""))
            if not cat:
                continue
            for v in p.get("variants", []):
                size = v.get("title") if v.get("title") not in (None, "Default Title") else ""
                out.append({
                    "key": f"{brand}|{p['handle']}|{v.get('id')}",
                    "brand": brand, "product": p.get("title", ""), "size": size,
                    "category": cat,
                    "price": float(v["price"]) if v.get("price") else None,
                    "mrp": float(v["compare_at_price"]) if v.get("compare_at_price") else None,
                    "in_stock": bool(v.get("available", True)),
                    "url": f"{base.rstrip('/')}/products/{p['handle']}",
                    "source": "Brand store",
                })
        page += 1
    return out


def sitemap_urls(url, depth=0):
    root = ET.fromstring(fetch(url))
    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    locs = [e.text.strip() for e in root.iter(ns + "loc") if e.text]
    if root.tag.endswith("sitemapindex") and depth < 2:
        urls = []
        for sub in locs:
            if re.search(r"product|pdp|sitemap", sub, re.I):
                try:
                    urls += sitemap_urls(sub, depth + 1)
                except Exception:
                    pass
        return urls
    return locs


def sitemap(brand, cfg):
    match = [m.lower() for m in cfg.get("match", [])]
    out = []
    for u in sitemap_urls(cfg["sitemap"]):
        lu = u.lower()
        if match and not any(m in lu for m in match):
            continue
        slug = re.sub(r"[-_]+", " ", lu.rstrip("/").split("/")[-1].split(".")[0]).strip()
        cat = classify(slug) if slug else None
        if not cat:
            continue
        out.append({"key": f"{brand}|{u}", "brand": brand, "product": slug.title(), "size": "",
                    "category": cat, "price": None, "mrp": None,
                    "in_stock": None, "url": u, "source": "Brand website"})
    return out


def diff(old, new, checked_brands):
    """Returns change events for the news feed."""
    o = {p["key"]: p for p in old}
    n = {p["key"]: p for p in new}
    changes = []
    def ev(p, title, tags):
        changes.append({"brand": p["brand"], "kind": "product", "title": title, "url": p["url"],
                        "publisher": p["source"], "published": NOW, "summary": p["category"], "tags": tags,
                        "source_type": "Official"})
    for k, p in n.items():
        name = f"{p['product']}{' (' + p['size'] + ')' if p['size'] else ''}"
        if k not in o:
            if any(x["brand"] == p["brand"] for x in old):   # skip the very first check of a brand
                ev(p, f"New product listed: {name}", ["Launch", "Product"])
            continue
        q = o[k]
        if p["price"] and q.get("price") and abs(p["price"] - q["price"]) >= 1:
            up = p["price"] > q["price"]
            ev(p, f"Price {'up' if up else 'down'}: {name} ₹{q['price']:.0f} → ₹{p['price']:.0f}", ["Pricing & Promo", "Product"])
        if q.get("in_stock") and p.get("in_stock") is False:
            ev(p, f"Out of stock: {name}", ["Product"])
    for k, q in o.items():
        if k not in n and q["brand"] in checked_brands:
            ev(q, f"Product no longer listed: {q['product']}", ["Product"])
    return changes


def run(log=print):
    """Check every brand; returns (change events, run log entries)."""
    old = json.loads(FILE.read_text()).get("products", []) if FILE.exists() else []
    # apply today's category rules to the saved list too, so items we now leave out
    # are not reported as "no longer listed"
    old = [dict(p, category=classify(p["product"])) for p in old if classify(p["product"])]
    new, run_log, checked = [], [], set()
    for b in CONFIG["brands"]:
        cfg = b.get("products") or {}
        if not cfg:
            continue
        label = f"Products: {b['name']}"
        try:
            got = shopify(b["name"], cfg["shopify"], cfg.get("vendor")) if cfg.get("shopify") else sitemap(b["name"], cfg)
            if not got:
                raise RuntimeError("no matching products found")
            new += got
            checked.add(b["name"])
            run_log.append({"source": label, "ok": True, "found": len(got)})
            log(f"OK   {label}: {len(got)}")
        except Exception as e:
            run_log.append({"source": label, "ok": False, "error": str(e)[:200]})
            log(f"FAIL {label}: {e}")
    # keep last known data for brands that failed this time
    keep = [p for p in old if p["brand"] not in checked]
    for p in new:
        prev = next((q for q in old if q["key"] == p["key"]), None)
        p["first_seen"] = prev.get("first_seen", NOW) if prev else NOW
        p["last_seen"] = NOW
    changes = diff(old, new, checked)
    DATA.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps({"updated": NOW, "products": sorted(keep + new, key=lambda p: (p["brand"], p["product"]))},
                               ensure_ascii=False, indent=0))
    return changes, run_log


if __name__ == "__main__":
    ch, _ = run()
    print(f"{len(ch)} changes")
