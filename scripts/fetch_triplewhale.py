#!/usr/bin/env python3
"""Fetch Natural Rems ad-level performance from Triple Whale, parse the naming
conventions, and emit the JSON manifest the dashboard reads.

Two input modes:

  API  (default, used by the GitHub Action)
      Needs TW_CLIENT_ID + TW_CLIENT_SECRET. Exchanges them for a bearer
      token, then pulls ad-level rows page by page.

  CSV  (--from-csv PATH)
      Builds the same manifest from a Creative Analysis CSV export. Used to
      stand the dashboard up before an API key exists; produces an identical
      output contract so the front end never knows the difference.

Output: public/data/latest.json (overwritten) + public/data/history/<stamp>.json.gz
"""
import argparse
import csv
import gzip
import io
import json
import os
import pathlib
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib import request as urlreq
from urllib.error import HTTPError

from parse_ad_name import parse_ad_name

API_BASE = "https://api.triplewhale.com/api/v2"
SHOP_DOMAIN = "18b070.myshopify.com"

# The four Meta ad accounts attached to the Natural Rems shop, read off the
# Creative Analysis request payload.
AD_ACCOUNTS = [
    "act_426535909947221",
    "act_3659143914352798",
    "act_944402037249956",
    "act_867775032781666",
]

# Triple Whale serves creative thumbnails from its own CDN, keyed by Meta ad
# id — which is why this dashboard needs no Meta token to show previews.
THUMB_CDN = "https://files.triplewhale.com/thumbnails/facebook-ads"

LOOKBACK_DAYS = 180
PAGE_SIZE = 500
SERVICE_ID = "facebook-ads"

# Attribution lens. Platform-reported (what Meta says) is primary so the
# numbers tie out to Ads Manager; Triple Whale's pixel attribution is carried
# alongside as the secondary lens.
PIXEL_ATTRIBUTION_MODEL = "lastPlatformClick"
ATTRIBUTION_WINDOW = "lifetime"

COLUMNS = [
    "preview", "type", "campaign", "adset", "spend", "conversionValue",
    "pixelConversionValue", "roas", "pixelRoas", "purchases", "pixelPurchases",
    "cpa", "pixelCpa", "aov", "pixelAov", "clicks", "impressions", "cpc",
    "ctr", "cpm", "outboundClicks", "outboundCtr", "pixelNcRoas",
    "pixelNcPurchases", "pixelNcConversionValue",
]


# --- HTTP ------------------------------------------------------------------
def _post(url: str, body: dict, headers: dict, timeout: int = 180) -> dict:
    req = urlreq.Request(url, data=json.dumps(body).encode(),
                         headers={**headers, "Content-Type": "application/json"},
                         method="POST")
    for attempt in range(5):
        try:
            with urlreq.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except HTTPError as e:
            if e.code == 429 and attempt < 4:
                wait = 20 * (attempt + 1)
                print(f"  rate-limited, waiting {wait}s…", flush=True)
                time.sleep(wait)
                continue
            print(f"  HTTP {e.code}: {e.read().decode()[:400]}", flush=True)
            raise
    raise RuntimeError("exhausted retries")


def get_bearer() -> str:
    cid = os.environ.get("TW_CLIENT_ID")
    secret = os.environ.get("TW_CLIENT_SECRET")
    if not cid or not secret:
        sys.exit("ERROR: TW_CLIENT_ID / TW_CLIENT_SECRET not set "
                 "(or pass --from-csv to build from a CSV export)")
    res = _post(f"{API_BASE}/auth/gen-oauth-token",
                {"clientId": cid, "clientSecret": secret}, {})
    token = res.get("access_token") or res.get("accessToken") or res.get("token")
    if not token:
        sys.exit(f"ERROR: no token in auth response: {str(res)[:300]}")
    return token


def fetch_pages(token: str, start: str, end: str) -> list:
    """Page through ad-level rows. Mirrors the payload the Creative Analysis
    dashboard itself sends."""
    headers = {"Authorization": f"Bearer {token}"}
    rows, page = [], 0
    while True:
        payload = {
            "start": start, "end": end,
            "service_id": SERVICE_ID, "data_type": "ads-metrics",
            "account_ids": AD_ACCOUNTS,
            "granularity": "day", "entity": "ad", "creative_type": "ad",
            "sort_by": "spend", "sort_direction": "descending",
            "page": page, "pageSize": PAGE_SIZE,
            "filters": [], "segments": [], "currency": "USD",
            "pixel_attribution_model": PIXEL_ATTRIBUTION_MODEL,
            "pixel_date_model": "eventDate",
            "attributionWindow": ATTRIBUTION_WINDOW,
            "subscriptionTags": ["oneTime", "subscriptionFirstOrder",
                                 "subscriptionRecurringOrder"],
            "shopDomain": SHOP_DOMAIN,
            "selectedColumns": COLUMNS,
        }
        res = _post(f"{API_BASE}/attribution/get-creatives-breakdown",
                    payload, headers)
        batch = res if isinstance(res, list) else (
            res.get("data") or res.get("rows") or res.get("creatives") or [])
        if not batch:
            break
        rows.extend(batch)
        print(f"  page {page}: {len(batch)} rows (total {len(rows)})", flush=True)
        if len(batch) < PAGE_SIZE:
            break
        page += 1
        if page > 200:
            print("  safety stop at 200 pages", flush=True)
            break
    return rows


# --- Normalisation ---------------------------------------------------------
def _f(v) -> float:
    if v is None or v == "" or v == "-":
        return 0.0
    try:
        return float(str(v).replace("$", "").replace(",", "").replace("%", ""))
    except ValueError:
        return 0.0


def _pick(row: dict, *names):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return None


def normalise(raw_rows: list) -> list:
    """Collapse rows into one record per creative and attach parsed dimensions.

    Rows arrive per (ad, day). Summing them gives lifetime totals for the
    window. `launch_date` is the first day the creative recorded spend — a far
    more reliable launch signal than the date tokens in the names, which only
    about a third of Natural Rems ads carry.
    """
    by_key: dict[str, dict] = {}
    first_seen: dict[str, str] = {}

    for row in raw_rows:
        name = str(_pick(row, "name", "ad_name", "adName", "creative_name") or "").strip()
        if not name:
            continue
        campaign = _pick(row, "campaign", "campaign_name", "campaignName")
        adset = _pick(row, "adset", "adset_name", "adsetName")
        ad_id = str(_pick(row, "ad_id", "adId", "id", "entity_id") or "").strip()
        day = str(_pick(row, "date", "day", "event_date") or "")[:10]

        parsed = parse_ad_name(name, campaign, adset)
        key = parsed["dedup_key"] or name

        metrics = {
            "spend": _f(_pick(row, "spend")),
            "impressions": _f(_pick(row, "impressions", "imprs")),
            "clicks": _f(_pick(row, "clicks")),
            "outbound_clicks": _f(_pick(row, "outboundClicks", "outbound_clicks")),
            # Platform-reported (Meta) — primary lens.
            "plat_rev": _f(_pick(row, "conversionValue", "conversion_value")),
            "plat_txns": _f(_pick(row, "purchases")),
            # Triple Whale pixel attribution — secondary lens.
            "pixel_rev": _f(_pick(row, "pixelConversionValue", "pixel_conversion_value")),
            "pixel_txns": _f(_pick(row, "pixelPurchases", "pixel_purchases")),
            "nc_rev": _f(_pick(row, "pixelNcConversionValue")),
            "nc_txns": _f(_pick(row, "pixelNcPurchases")),
        }

        if key in by_key:
            agg = by_key[key]
            for k, v in metrics.items():
                agg["metrics"][k] += v
            if campaign:
                agg["_campaigns"].add(campaign)
            if adset:
                agg["_adsets"].add(adset)
            if ad_id.isdigit():
                agg["_ad_ids"].add(ad_id)
        else:
            by_key[key] = {
                **parsed,
                "metrics": metrics,
                "_campaigns": {campaign} if campaign else set(),
                "_adsets": {adset} if adset else set(),
                "_ad_ids": {ad_id} if ad_id.isdigit() else set(),
            }
        # Track the earliest day this creative spent anything.
        if day and metrics["spend"] > 0:
            prev = first_seen.get(key)
            if prev is None or day < prev:
                first_seen[key] = day

    out = []
    for key, rec in by_key.items():
        m = rec["metrics"]
        m["roas"] = round(m["plat_rev"] / m["spend"], 4) if m["spend"] else 0
        m["pixel_roas"] = round(m["pixel_rev"] / m["spend"], 4) if m["spend"] else 0
        m["cpm"] = round((m["spend"] / m["impressions"]) * 1000, 4) if m["impressions"] else 0
        m["cpc"] = round(m["spend"] / m["clicks"], 4) if m["clicks"] else None
        m["ctr"] = round((m["clicks"] / m["impressions"]) * 100, 4) if m["impressions"] else None
        m["cpa"] = round(m["spend"] / m["plat_txns"], 4) if m["plat_txns"] else None
        m["aov"] = round(m["plat_rev"] / m["plat_txns"], 2) if m["plat_txns"] else None
        m["pct_new"] = round((m["nc_txns"] / m["pixel_txns"]) * 100, 2) if m["pixel_txns"] else None
        # Names the front end reads as the primary lens.
        m["rev"] = m["plat_rev"]
        m["txns"] = m["plat_txns"]

        # Launch date: first day with spend wins; fall back to a date parsed
        # out of the campaign or ad name.
        iso = first_seen.get(key)
        if iso:
            rec["launch_date"] = iso
            rec["date"] = iso[2:4] + iso[5:7] + iso[8:10]
        elif rec.get("date"):
            d = rec["date"]
            rec["launch_date"] = f"20{d[0:2]}-{d[2:4]}-{d[4:6]}"

        ad_ids = sorted(rec.pop("_ad_ids"))
        rec["meta_ad_ids"] = ad_ids
        rec["campaigns"] = sorted(x for x in rec.pop("_campaigns") if x)
        rec["adsets"] = sorted(x for x in rec.pop("_adsets") if x)
        if ad_ids:
            # Thumbnails are served per ad account and we do not know which
            # account owns this ad, so ship every candidate and let the img
            # onError chain fall through to the next one.
            rec["thumbnail_candidates"] = [
                f"{THUMB_CDN}/{acct}/{ad_ids[0]}.jpg" for acct in AD_ACCOUNTS
            ]
            rec["thumbnail_url"] = rec["thumbnail_candidates"][0]
            rec["preview_link"] = (
                "https://business.facebook.com/adsmanager/manage/ads"
                f"?selected_ad_ids={ad_ids[0]}"
            )
        out.append(rec)
    return out


def rows_from_csv(path: pathlib.Path) -> list:
    text = path.read_text(errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-csv", type=pathlib.Path,
                    help="Build from a Creative Analysis CSV export instead of the API")
    ap.add_argument("--lookback", type=int, default=LOOKBACK_DAYS)
    args = ap.parse_args()

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    out_dir = repo_root / "public" / "data"
    history_dir = out_dir / "history"
    out_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=args.lookback)
    start_s, end_s = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    if args.from_csv:
        print(f"Reading CSV export: {args.from_csv}")
        raw = rows_from_csv(args.from_csv)
        source = f"csv:{args.from_csv.name}"
    else:
        print(f"Fetching Triple Whale ad-level data: {start_s} → {end_s}")
        print(f"Shop: {SHOP_DOMAIN} · {len(AD_ACCOUNTS)} Meta ad accounts")
        token = get_bearer()
        raw = fetch_pages(token, start_s, end_s)
        source = "triplewhale-api"
    print(f"  {len(raw)} raw rows")

    ads = normalise(raw)
    print(f"  {len(ads)} unique creatives")

    known = sum(1 for a in ads if a.get("convention") != "unknown")
    dated = sum(1 for a in ads if a.get("launch_date"))
    print(f"  parsed convention: {known}/{len(ads)} ({known/max(len(ads),1)*100:.1f}%)")
    print(f"  launch date:       {dated}/{len(ads)} ({dated/max(len(ads),1)*100:.1f}%)")

    manifest = {
        "generated_at": now.isoformat(),
        "period": {"start": start_s, "end": end_s},
        "source": source,
        "shop": SHOP_DOMAIN,
        "attribution": {
            "primary": "Platform-reported (Meta)",
            "secondary": "Triple Whale pixel — lastPlatformClick",
            "window": ATTRIBUTION_WINDOW,
        },
        "ad_accounts": AD_ACCOUNTS,
        "ad_count": len(ads),
        "ads": ads,
    }

    latest = out_dir / "latest.json"
    latest.write_text(json.dumps(manifest, separators=(",", ":"), default=str))
    print(f"Wrote {latest} ({latest.stat().st_size // 1024} KB)")

    snap = history_dir / f"{now.strftime('%Y-%m-%d_%H%M')}.json.gz"
    with gzip.open(snap, "wt") as f:
        json.dump(manifest, f, separators=(",", ":"), default=str)
    print(f"Snapshot → {snap}")


if __name__ == "__main__":
    main()
