#!/usr/bin/env python3
"""Pull every input the dashboard build needs straight from the Triple Whale
SQL API, so the refresh runs unattended (no Meta export, no SQL editor).

    TW_API_KEY=... python3 scripts/fetch_tw_sql.py [--days 120] [--out raw/]

Writes, in the exact formats build_from_meta_csv.py already reads:

    meta_ads.csv     one row per Meta ad (names, campaign, ad set, Meta-reported
                     purchases/value) in the Ads Manager column layout
    tw_ads.csv       per-ad pixel totals for the window
    tw_launch.csv    lifetime first spend day, day-0..13 spend/rev/NC, thumbnail
    tw_weekly.csv    per-ad Monday weeks
    tw_daily.csv     per-ad daily rows (the metric source for date filtering)

then runs the build. The window ends yesterday (UTC) so a partial day never
lands in the numbers.

API: POST /api/v2/orcabase/api/sql with header x-api-key. The response is a
bare JSON array of row objects (some accounts get a {"data": [...]} wrapper;
both are handled).
"""
import argparse
import csv
import json
import os
import pathlib
import subprocess
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from urllib import request as urlreq
from urllib.error import HTTPError

API = "https://api.triplewhale.com/api/v2/orcabase/api/sql"
SHOP = "18b070.myshopify.com"
ACCOUNT_ID = "944402037249956"
LAUNCH_LOOKBACK = "2024-01-01"   # far enough back to find any live ad's first spend day
CHUNK_DAYS = 7
ROOT = pathlib.Path(__file__).resolve().parent.parent

BASE_WHERE = (
    "channel = 'facebook-ads' AND model = 'Triple Attribution' AND attribution_window = '7_days' "
    f"AND account_id IN ('{ACCOUNT_ID}', 'act_{ACCOUNT_ID}')"
)


def sql(query, start, end, key, tries=4):
    body = json.dumps({"shopId": SHOP, "query": query,
                       "period": {"startDate": start, "endDate": end}}).encode()
    req = urlreq.Request(API, data=body, method="POST", headers={
        "x-api-key": key, "content-type": "application/json", "accept": "application/json",
        "user-agent": "Mozilla/5.0 (natural-rems-dashboard refresh)"})
    for attempt in range(tries):
        try:
            with urlreq.urlopen(req, timeout=300) as r:
                data = json.load(r)
            if isinstance(data, dict):
                data = data.get("data") or data.get("rows") or []
            return data
        except HTTPError as e:
            msg = e.read().decode(errors="replace")[:500]
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(15 * (attempt + 1))
                continue
            sys.exit(f"Triple Whale SQL API {e.code}: {msg}")
    return []


def chunks(start, end):
    d = start
    while d <= end:
        e = min(d + timedelta(days=CHUNK_DAYS - 1), end)
        yield d, e
        d = e + timedelta(days=1)


def f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def fmt(v):
    v = round(f(v), 2)
    return str(int(v)) if v == int(v) else str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=int(os.environ.get("WINDOW_DAYS", 120)))
    ap.add_argument("--out", default=str(ROOT / "raw"))
    ap.add_argument("--no-build", action="store_true")
    a = ap.parse_args()
    key = os.environ.get("TW_API_KEY")
    if not key:
        sys.exit("TW_API_KEY is not set")

    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = end - timedelta(days=a.days - 1)
    s, e = start.isoformat(), end.isoformat()
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"window {s} → {e}")

    # 1. Daily per-ad rows, a week at a time (keeps each response small).
    daily = []
    for cs, ce in chunks(start, end):
        rows = sql(f"""
            SELECT ad_id, toString(event_date) AS d,
                   sum(spend) AS spend, sum(order_revenue) AS rev, sum(orders_quantity) AS orders,
                   sum(new_customer_order_revenue) AS nc_rev, sum(new_customer_orders) AS nc_orders,
                   sum(new_visitors) AS nv, sum(unique_visitors) AS vis,
                   sum(impressions) AS imps, sum(clicks) AS clk
            FROM pixel_joined_tvf()
            WHERE {BASE_WHERE} AND event_date BETWEEN '{cs}' AND '{ce}'
            GROUP BY ad_id, d
            HAVING spend > 0 OR rev > 0""", cs.isoformat(), ce.isoformat(), key)
        print(f"  daily {cs} → {ce}: {len(rows)} rows")
        daily += rows

    # 2. Names / campaign / ad set and Meta-reported conversions for the window.
    meta = sql(f"""
        SELECT ad_id, argMax(ad_name, event_date) AS ad_name,
               argMax(campaign_name, event_date) AS campaign, argMax(adset_name, event_date) AS adset,
               sum(spend) AS spend, sum(channel_reported_conversion_value) AS meta_rev,
               sum(channel_reported_conversions) AS meta_orders,
               sum(impressions) AS imps, sum(clicks) AS clk
        FROM pixel_joined_tvf()
        WHERE {BASE_WHERE} AND event_date BETWEEN '{s}' AND '{e}'
        GROUP BY ad_id HAVING spend > 0""", s, e, key)
    print(f"  ads: {len(meta)}")

    # 3. Lifetime first spend day + thumbnail for every ad in the window.
    launch = sql(f"""
        SELECT ad_id, toString(minIf(event_date, spend > 0)) AS first_day,
               argMax(ad_image_url, event_date) AS image_url
        FROM pixel_joined_tvf()
        WHERE {BASE_WHERE} AND event_date BETWEEN '{LAUNCH_LOOKBACK}' AND '{e}'
          AND ad_id IN (SELECT DISTINCT ad_id FROM pixel_joined_tvf()
                        WHERE {BASE_WHERE} AND event_date BETWEEN '{s}' AND '{e}' AND spend > 0)
        GROUP BY ad_id""", LAUNCH_LOOKBACK, e, key)
    print(f"  launch: {len(launch)}")

    if not daily or not meta:
        sys.exit("Triple Whale returned no rows — refusing to overwrite the dashboard")

    by_ad = defaultdict(list)
    for r in daily:
        by_ad[str(r["ad_id"])].append(r)

    with open(out / "tw_daily.csv", "w") as fh:
        fh.write("ad_id;date;spend;rev;orders;nc_rev;nc_orders;new_visitors;visitors;impressions;clicks\n")
        for r in sorted(daily, key=lambda r: (str(r["ad_id"]), r["d"])):
            fh.write(";".join([str(r["ad_id"]), r["d"][:10]] + [fmt(r[k]) for k in
                     ("spend", "rev", "orders", "nc_rev", "nc_orders", "nv", "vis", "imps", "clk")]) + "\n")

    wk = defaultdict(lambda: [0.0] * 4)
    for r in daily:
        d = date.fromisoformat(r["d"][:10])
        v = wk[(str(r["ad_id"]), (d - timedelta(days=d.weekday())).isoformat())]
        v[0] += f(r["spend"]); v[1] += f(r["rev"]); v[2] += f(r["nc_rev"]); v[3] += f(r["nc_orders"])
    with open(out / "tw_weekly.csv", "w") as fh:
        fh.write("ad_id;week;spend;rev;nc_rev;nc_orders\n")
        for (ad, w), v in sorted(wk.items()):
            fh.write(";".join([ad, w] + [fmt(x) for x in v]) + "\n")

    # Day 0–13 from the launch day, only computable when the launch is inside the
    # window — exactly the creatives launch win rates count.
    with open(out / "tw_launch.csv", "w") as fh:
        fh.write("ad_id;first_day;spend_d14;rev_d14;nc_rev_d14;image_url\n")
        for r in launch:
            ad, fd = str(r["ad_id"]), (r.get("first_day") or "")[:10]
            s14 = r14 = n14 = 0.0
            if fd and fd >= s:
                cut = (date.fromisoformat(fd) + timedelta(days=13)).isoformat()
                for x in by_ad.get(ad, []):
                    if x["d"][:10] <= cut:
                        s14 += f(x["spend"]); r14 += f(x["rev"]); n14 += f(x["nc_rev"])
            fh.write(";".join([ad, fd, fmt(s14), fmt(r14), fmt(n14), r.get("image_url") or ""]) + "\n")

    first = {str(r["ad_id"]): (r.get("first_day") or "")[:10] for r in launch}
    with open(out / "tw_ads.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ad_id", "spend", "pixel_rev", "pixel_orders", "nc_rev", "nc_orders",
                    "new_visitors", "visitors", "sessions", "first_day"])
        for ad, rs in by_ad.items():
            t = lambda k: fmt(sum(f(x[k]) for x in rs))
            w.writerow([ad, t("spend"), t("rev"), t("orders"), t("nc_rev"), t("nc_orders"),
                        t("nv"), t("vis"), 0, first.get(ad, "")])

    with open(out / "meta_ads.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Reporting starts", "Reporting ends", "Ad name", "Attribution setting",
                    "Amount spent (USD)", "Impressions", "Link clicks", "Purchases",
                    "Purchases conversion value", "Ad ID", "Campaign name", "Ad set name"])
        for r in meta:
            w.writerow([s, e, r.get("ad_name") or str(r["ad_id"]), "Meta-reported (via Triple Whale)",
                        fmt(r["spend"]), fmt(r["imps"]), fmt(r["clk"]), fmt(r["meta_orders"]),
                        fmt(r["meta_rev"]), str(r["ad_id"]), r.get("campaign") or "", r.get("adset") or ""])

    tot = sum(f(r["spend"]) for r in daily)
    print(f"spend ${tot:,.0f} across {len(by_ad)} ads, {len(daily)} ad-days")
    if a.no_build:
        return
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_from_meta_csv.py"),
                    str(out / "meta_ads.csv"), str(ROOT / "data" / "master_tags.json"),
                    str(out / "tw_ads.csv"), str(out / "tw_launch.csv"),
                    str(out / "tw_weekly.csv"), str(out / "tw_daily.csv")], check=True)


if __name__ == "__main__":
    main()
