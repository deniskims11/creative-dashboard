#!/usr/bin/env python3
"""Build the dashboard manifest from a Meta Ads Manager ad-level CSV export.

The team renamed the account's ads to a pipe-delimited taxonomy:

    Ad No | Product | Angle | Sub-Angle | Concept | ICP | Script | Format | Awareness
          [| Strategist [| Editor [| Hook]]]

so the dimensions are read straight off the ad name. Ads that still carry a
legacy name fall back to the reviewed MASTER workbook (matched on Meta ad id,
then on the old name), and otherwise show as "(untagged)".

The same creative usually runs as several Meta ads (duplicated into other ad
sets, "- Copy" suffixes), so rows are rolled up to one record per creative.

Meta export columns used: Ad name, Ad ID, Campaign name, Ad set name,
Amount spent (USD), Purchases, Purchases conversion value, Impressions,
Link clicks, Reporting starts / ends, Attribution setting.

Optionally joins a Triple Whale per-ad export (SQL editor, pixel_joined_tvf,
channel facebook-ads, Triple Attribution, 7_days) on Meta ad id:
    ad_id,spend,pixel_rev,pixel_orders,nc_rev,nc_orders,new_visitors,visitors,sessions,first_day
When present, Triple Whale pixel revenue/orders are the primary lens, NC
metrics and new-visitor share are filled in, and the launch date is the ad's
first spend day. Without it, revenue is Meta-attributed and has_nc = False.
"""
import csv
import gzip
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from parse_ad_name import parse_ad_name, parse_campaign

ACCOUNT = "act_944402037249956"
THUMB_CDN = "https://files.triplewhale.com/thumbnails/facebook-ads"
TAXONOMY = ["creative_no", "product", "angle", "sub_angle", "concept", "icp",
            "script", "format", "awareness_stage"]
EXTRA = ["strategist", "editor", "hook"]
COPY_RE = re.compile(r"\s*[-–]\s*Copy(\s*\d+)?\s*$", re.I)


def num(v):
    try:
        return float(str(v).replace("$", "").replace(",", "")) if v not in (None, "") else 0.0
    except ValueError:
        return 0.0


def clean(name):
    """Strip any number of trailing '- Copy' / '- Copy 2' suffixes."""
    name = (name or "").strip()
    while COPY_RE.search(name):
        name = COPY_RE.sub("", name).strip()
    return name


def parse_pipe(name):
    parts = [p.strip() for p in name.split("|")]
    if len(parts) < 9:
        return None
    out = {k: (clean(parts[i]) if i == 0 else parts[i]) or None for i, k in enumerate(TAXONOMY)}
    for i, k in enumerate(EXTRA, start=9):
        out[k] = parts[i] if len(parts) > i and parts[i] else None
    return out


def parse_nr(name):
    """NR_10.15.26_[batch_]TY_Video_Sea_Moss_Gummies_Green_Apple_<Concept>_TOF_1[_HookA]"""
    m = re.match(r"NR_(\d{2})\.(\d{2})\.(\d{2})_", name)
    if not m:
        return None
    toks = name.split("_")
    fmt = next((t for t in toks if t in ("Video", "Static")), None)
    stage = next((t for t in toks if t in ("TOF", "MOF", "BOF")), None)
    return {"format": fmt, "funnel": stage,
            "date": f"{m.group(3)}{m.group(1)}{m.group(2)}"}


def load_master(path):
    if not path or not path.exists():
        return {}, {}
    from xlsx_read import read_sheet
    rows = read_sheet(str(path), "xl/worksheets/sheet1.xml")
    head = [str(h or "").strip() for h in rows[0]]
    ix = {h: i for i, h in enumerate(head)}
    cols = {"creative_no": "Ad Number", "product": "Product", "angle": "Angle",
            "sub_angle": "Sub-Angle", "concept": "Concept", "icp": "ICP",
            "script": "Script", "format": "Format", "awareness_stage": "Awareness Stage"}
    by_id, by_name = {}, {}
    for r in rows[1:]:
        get = lambda c: (str(r[ix[c]]).strip() if c in ix and ix[c] < len(r) and r[ix[c]] not in (None, "") else None)
        rec = {k: get(c) for k, c in cols.items()}
        if not rec["angle"]:
            continue
        if get("Ad ID"):
            by_id[get("Ad ID")] = rec
        if get("Previous Ad Name"):
            by_name[clean(get("Previous Ad Name"))] = rec
    return by_id, by_name


def load_tw(path):
    if not path or not path.exists():
        return {}
    return {r["ad_id"]: r for r in csv.DictReader(open(path, encoding="utf-8-sig"))}


# Legacy parser codes -> the renamed taxonomy's angle names.
LEGACY_ANGLE = {"MUCUS": "Mucus", "JOINT": "Joint", "HEART": "Heart Health",
                "BLOOD PRESSURE": "Heart Health", "BLOOD FLOW": "Heart Health",
                "TESTO": "Testosterone", "SUPERFOOD": "Superfood", "KIDNEY": "Kidney",
                "THYROID": "Thyroid", "GUM": "Gum", "WEIGHT LOSS": "Weight Loss",
                "SEA MOSS": None}
MATURE_DAYS = 14
AWARENESS = ["Unaware", "Problem Aware", "Solution Aware", "Product Aware", "Most Aware"]
FUNNEL_OF = {"Unaware": "TOF", "Problem Aware": "TOF", "Solution Aware": "MOF",
             "Product Aware": "BOF", "Most Aware": "BOF"}


def norm_awareness(v):
    """'Problem Aware-BED-RITUAL_V1', 'Problem Aware.' -> 'Problem Aware'."""
    if not v:
        return None
    low = v.lower()
    return next((a for a in AWARENESS if low.startswith(a.lower())), v)


def load_semi(path, cols):
    """Semicolon files exported from the Triple Whale SQL editor (header row first)."""
    if not path or not path.exists():
        return []
    lines = path.read_text().splitlines()
    return [dict(zip(cols, l.split(";", len(cols) - 1))) for l in lines[1:] if l.strip()]


def build(csv_path, master_path, tw_path=None, launch_path=None, weekly_path=None):
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    tw = load_tw(tw_path)
    launch = {r["ad_id"]: r for r in load_semi(
        launch_path, ["ad_id", "first_day", "spend_d14", "rev_d14", "nc_rev_d14", "image_url"])}
    weekly = defaultdict(list)
    for r in load_semi(weekly_path, ["ad_id", "week", "spend", "rev", "nc_rev", "nc_orders"]):
        weekly[r["ad_id"]].append(r)
    weeks = sorted({r["week"] for rs in weekly.values() for r in rs})
    widx = {w: i for i, w in enumerate(weeks)}
    by_id, by_name = load_master(master_path)
    starts = sorted(r["Reporting starts"] for r in rows if r.get("Reporting starts"))
    ends = sorted(r["Reporting ends"] for r in rows if r.get("Reporting ends"))
    attribution = Counter(r.get("Attribution setting") for r in rows).most_common(1)[0][0]

    groups = defaultdict(list)
    for r in rows:
        if num(r.get("Amount spent (USD)")) <= 0:
            continue
        groups[clean(r["Ad name"])].append(r)

    ads = []
    for key, rs in groups.items():
        spend = sum(num(r["Amount spent (USD)"]) for r in rs)
        rev = sum(num(r["Purchases conversion value"]) for r in rs)
        txns = sum(num(r["Purchases"]) for r in rs)
        imps = sum(num(r["Impressions"]) for r in rs)
        clicks = sum(num(r["Link clicks"]) for r in rs)
        ad_ids = [r["Ad ID"] for r in sorted(rs, key=lambda r: -num(r["Amount spent (USD)"]))]
        twr = [tw[i] for i in ad_ids if i in tw]
        campaigns = sorted({r["Campaign name"] for r in rs if r.get("Campaign name")})
        adsets = sorted({r["Ad set name"] for r in rs if r.get("Ad set name")})
        top = max(rs, key=lambda r: num(r["Amount spent (USD)"]))

        tags = parse_pipe(top["Ad name"])
        status, convention = "Renamed — taxonomy in ad name", "renamed"
        if not tags:
            m = next((by_id[i] for i in ad_ids if i in by_id), None) or by_name.get(key)
            if m:
                tags = dict(m)
                status, convention = "Legacy name — tagged from MASTER workbook", "master"
            else:
                tags = {}
                status, convention = "Legacy name — untagged", "untagged"
                legacy = parse_ad_name(top["Ad name"], top.get("Campaign name"))
                ang = LEGACY_ANGLE.get(legacy.get("angle") or "", None)
                fmt = legacy.get("format")
                if ang or fmt:
                    tags = {"angle": ang, "format": fmt.title() if fmt else None,
                            "creative_no": legacy.get("creative_no")}
                    status, convention = "Legacy name — angle/format inferred from name", "inferred"
        nr = parse_nr(key)
        if nr and not tags.get("format"):
            tags["format"] = nr["format"]

        camps = [parse_campaign(c) for c in campaigns]
        lr = [launch[i] for i in ad_ids if i in launch]
        first = sorted([t["first_day"] for t in lr if t.get("first_day")] or
                       [t["first_day"] for t in twr if t.get("first_day")])
        if first:
            dates = [first[0][2:4] + first[0][5:7] + first[0][8:10]]
        else:
            dates = sorted(d for d in [c["launch_date"] for c in camps] + [nr and nr["date"]] if d)
        camp = parse_campaign(top.get("Campaign name"))

        meta_rev, meta_txns = rev, txns
        if tw:
            rev = sum(num(t["pixel_rev"]) for t in twr)
            txns = sum(num(t["pixel_orders"]) for t in twr)
        nc_rev = sum(num(t["nc_rev"]) for t in twr)
        nc_txns = sum(num(t["nc_orders"]) for t in twr)
        new_vis = sum(num(t["new_visitors"]) for t in twr)
        vis = sum(num(t["visitors"]) for t in twr)
        metrics = {
            "spend": round(spend, 2), "rev": round(rev, 2), "txns": txns,
            "pixel_rev": round(rev, 2), "pixel_txns": txns,
            "meta_rev": round(meta_rev, 2), "meta_txns": meta_txns,
            "nc_rev": round(nc_rev, 2), "nc_txns": nc_txns,
            "ncroas": round(nc_rev / spend, 4) if (tw and spend) else None,
            "nccpa": round(spend / nc_txns, 2) if nc_txns else None,
            "new_visitors": new_vis, "visitors": vis,
            "pct_new_visitors": round(new_vis / vis * 100, 2) if vis else None,
            "impressions": imps, "clicks": clicks,
        }
        metrics["roas"] = round(rev / spend, 4) if spend else 0
        metrics["pixel_roas"] = metrics["roas"]
        metrics["meta_roas"] = round(meta_rev / spend, 4) if spend else 0
        metrics["pct_new"] = round(nc_txns / txns * 100, 2) if (tw and txns) else None
        metrics["aov"] = round(rev / txns, 2) if txns else None
        metrics["cpa"] = round(spend / txns, 4) if txns else None
        metrics["cpm"] = round(spend / imps * 1000, 4) if imps else 0
        metrics["cpc"] = round(spend / clicks, 4) if clicks else None
        metrics["ctr"] = round(clicks / imps * 100, 4) if imps else None
        if lr:
            s14 = sum(num(t["spend_d14"]) for t in lr)
            n14 = sum(num(t["nc_rev_d14"]) for t in lr)
            metrics["spend_d14"] = round(s14, 2)
            metrics["nc_rev_d14"] = round(n14, 2)
            metrics["ncroas_d14"] = round(n14 / s14, 4) if s14 else None

        wk = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
        for i in ad_ids:
            for r in weekly.get(i, []):
                v = wk[widx[r["week"]]]
                v[0] += num(r["spend"]); v[1] += num(r["rev"]); v[2] += num(r["nc_rev"]); v[3] += num(r["nc_orders"])
        image = next((t["image_url"] for t in lr if t.get("image_url")), None)

        tags["awareness_stage"] = norm_awareness(tags.get("awareness_stage"))
        funnel = (nr and nr["funnel"]) or FUNNEL_OF.get(tags.get("awareness_stage"))
        rec = {
            "ad_name": key,
            "new_ad_name": key if convention == "renamed" else None,
            **{k: tags.get(k) for k in TAXONOMY + EXTRA},
            "funnel": funnel,
            "naming_status": status, "reviewed": convention != "untagged",
            "convention": convention,
            "campaign": top.get("Campaign name"), "adset": top.get("Ad set name"),
            "campaigns": campaigns, "adsets": adsets, "meta_ad_count": len(rs),
            "buying_type": camp["buying_type"], "batch": camp["batch"],
            "attribution_window": camp["attribution_window"], "geo": camp["geo"],
            "date": dates[0] if dates else None,
            "launch_date": (f"20{dates[0][:2]}-{dates[0][2:4]}-{dates[0][4:]}" if dates else None),
            "meta_ad_ids": ad_ids, "metrics": metrics,
            "wk": [[k, round(v[0], 2), round(v[1], 2), round(v[2], 2), v[3]] for k, v in sorted(wk.items())],
            "thumbnail_candidates": ([image] if image else []) + [f"{THUMB_CDN}/{ACCOUNT}/{i}.jpg" for i in ad_ids[:2]],
            "thumbnail_url": image or f"{THUMB_CDN}/{ACCOUNT}/{ad_ids[0]}.jpg",
            "preview_link": f"https://business.facebook.com/adsmanager/manage/ads?selected_ad_ids={ad_ids[0]}",
        }
        ads.append(rec)

    # Launch status relative to the reporting window: carry-over creatives were
    # already spending before it started; immature ones launched too recently to
    # have had a fair chance to scale. Both are excluded from launch win rates.
    start_yy = starts[0][2:4] + starts[0][5:7] + starts[0][8:10]
    end_dt = datetime.strptime(ends[-1], "%Y-%m-%d")
    for a in ads:
        d = a["date"]
        a["carry_over"] = bool(d and d < start_yy)
        a["mature"] = bool(d and (end_dt - datetime.strptime("20" + d, "%Y%m%d")).days >= MATURE_DAYS)
        a["launch_status"] = ("Carry-over (live before window)" if a["carry_over"] else
                              "Launched in window" if a["mature"] else
                              f"Too new (< {MATURE_DAYS} days)")

    ads.sort(key=lambda a: -a["metrics"]["spend"])
    for i, a in enumerate(ads, 1):
        a["spend_rank"] = i

    n_ren = sum(1 for a in ads if a["convention"] == "renamed")
    tot = sum(a["metrics"]["spend"] for a in ads) or 1
    s_ren = sum(a["metrics"]["spend"] for a in ads if a["convention"] == "renamed")
    s_tag = sum(a["metrics"]["spend"] for a in ads if a["convention"] != "untagged")
    tot_rev = sum(a["metrics"]["rev"] for a in ads)
    tot_nc = sum(a["metrics"]["nc_rev"] for a in ads)
    return {
        "weeks": weeks,
        "mature_days": MATURE_DAYS,
        "baseline": {"roas": round(tot_rev / tot, 4), "ncroas": round(tot_nc / tot, 4)},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start": starts[0], "end": ends[-1]},
        "source": f"meta-ads-manager-export:{csv_path.name}",
        "shop": "18b070.myshopify.com",
        "has_nc": bool(tw),
        "attribution": ({"primary": "Triple Whale Pixel Joined · Triple Attribution · 7-day",
                         "secondary": f"New-customer (NCROAS / NCCPA); Meta-reported ({attribution}) kept as meta_rev",
                         "window": "7 days"} if tw else
                        {"primary": f"Meta Ads Manager · {attribution}",
                         "secondary": None, "window": attribution}),
        "ad_accounts": [ACCOUNT],
        "scope_note": (
            f"Every ad that spent in {ACCOUNT}, {starts[0]} → {ends[-1]}, rolled up to "
            f"{len(ads):,} creatives. {n_ren:,} carry the new taxonomy in their name "
            f"({s_ren/tot:.0%} of spend); legacy names are tagged from the MASTER "
            "workbook or, failing that, angle/format are inferred from the old name "
            f"({s_tag/tot:.0%} of spend tagged in total); the rest shows as (untagged). "
            + ("Revenue, ROAS and new-customer figures are Triple Whale (Triple Attribution, "
               "7-day); launch date = the ad's first spend day ever. Launch win rates count "
               f"only creatives launched in the window and at least {MATURE_DAYS} days old." if tw else
               "Revenue and ROAS are Meta-attributed; Triple Whale new-customer metrics "
               "are not in this export.")
        ),
        "ad_count": len(ads),
        "ads": ads,
    }


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: build_from_meta_csv.py <meta.csv> [MASTER.xlsx] [tw_ads.csv] "
                 "[tw_launch_d14.csv] [tw_weekly.csv]")
    src = pathlib.Path(sys.argv[1])
    master = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else None
    twp = pathlib.Path(sys.argv[3]) if len(sys.argv) > 3 else None
    lp = pathlib.Path(sys.argv[4]) if len(sys.argv) > 4 else None
    wp = pathlib.Path(sys.argv[5]) if len(sys.argv) > 5 else None
    root = pathlib.Path(__file__).resolve().parent.parent
    out = root / "public" / "data"
    (out / "history").mkdir(parents=True, exist_ok=True)

    m = build(src, master, twp, lp, wp)
    (out / "latest.json").write_text(json.dumps(m, separators=(",", ":"), default=str))
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    with gzip.open(out / "history" / f"{stamp}.json.gz", "wt") as f:
        json.dump(m, f, separators=(",", ":"), default=str)

    ads = m["ads"]
    n = len(ads)
    spend = sum(a["metrics"]["spend"] for a in ads)
    rev = sum(a["metrics"]["rev"] for a in ads)
    print(f"{n} creatives · {m['period']['start']} → {m['period']['end']}")
    print(f"spend ${spend:,.0f} · rev ${rev:,.0f} · ROAS {rev/spend:.2f}x")
    if m["has_nc"]:
        nc = sum(a["metrics"]["nc_rev"] for a in ads)
        meta = sum(a["metrics"]["meta_rev"] for a in ads)
        nv = sum(a["metrics"]["new_visitors"] for a in ads); v = sum(a["metrics"]["visitors"] for a in ads)
        matched = sum(a["metrics"]["spend"] for a in ads if a["metrics"]["visitors"] or a["metrics"]["rev"])
        print(f"NC rev ${nc:,.0f} (NCROAS {nc/spend:.2f}x) · Meta rev ${meta:,.0f} · new visitors {nv/v:.1%}"
              f" · spend with TW data {matched/spend:.1%}")
    for c in ("renamed", "master", "inferred", "untagged"):
        s = sum(a["metrics"]["spend"] for a in ads if a["convention"] == c)
        print(f"  {c:<9} {sum(1 for a in ads if a['convention']==c):>5} creatives  ${s:>12,.0f}  {s/spend:5.1%}")
    print("dimension coverage (by spend):")
    for d in TAXONOMY + EXTRA + ["date"]:
        s = sum(a["metrics"]["spend"] for a in ads if a.get(d))
        print(f"  {d:<16} {s/spend:5.1%}")


if __name__ == "__main__":
    main()
