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

Revenue here is Meta-attributed purchase value. Triple Whale's new-customer
metrics (NCROAS, NC revenue) are not in a Meta export, so the manifest sets
has_nc = False and the UI hides them.
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
from parse_ad_name import parse_campaign

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


def build(csv_path, master_path):
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
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
        nr = parse_nr(key)
        if nr and not tags.get("format"):
            tags["format"] = nr["format"]

        camps = [parse_campaign(c) for c in campaigns]
        dates = sorted(d for d in [c["launch_date"] for c in camps] + [nr and nr["date"]] if d)
        camp = parse_campaign(top.get("Campaign name"))

        metrics = {
            "spend": round(spend, 2), "rev": round(rev, 2), "txns": txns,
            "pixel_rev": round(rev, 2), "pixel_txns": txns,
            "nc_rev": 0, "nc_txns": 0, "ncroas": None, "nccpa": None,
            "impressions": imps, "clicks": clicks,
        }
        metrics["roas"] = round(rev / spend, 4) if spend else 0
        metrics["pixel_roas"] = metrics["roas"]
        metrics["aov"] = round(rev / txns, 2) if txns else None
        metrics["cpa"] = round(spend / txns, 4) if txns else None
        metrics["cpm"] = round(spend / imps * 1000, 4) if imps else 0
        metrics["cpc"] = round(spend / clicks, 4) if clicks else None
        metrics["ctr"] = round(clicks / imps * 100, 4) if imps else None

        rec = {
            "ad_name": key,
            "new_ad_name": key if convention == "renamed" else None,
            **{k: tags.get(k) for k in TAXONOMY + EXTRA},
            "funnel": nr["funnel"] if nr else None,
            "naming_status": status, "reviewed": convention != "untagged",
            "convention": convention,
            "campaign": top.get("Campaign name"), "adset": top.get("Ad set name"),
            "campaigns": campaigns, "adsets": adsets, "meta_ad_count": len(rs),
            "buying_type": camp["buying_type"], "batch": camp["batch"],
            "attribution_window": camp["attribution_window"], "geo": camp["geo"],
            "date": dates[0] if dates else None,
            "launch_date": (f"20{dates[0][:2]}-{dates[0][2:4]}-{dates[0][4:]}" if dates else None),
            "meta_ad_ids": ad_ids, "metrics": metrics,
            "thumbnail_candidates": [f"{THUMB_CDN}/{ACCOUNT}/{i}.jpg" for i in ad_ids[:3]],
            "thumbnail_url": f"{THUMB_CDN}/{ACCOUNT}/{ad_ids[0]}.jpg",
            "preview_link": f"https://business.facebook.com/adsmanager/manage/ads?selected_ad_ids={ad_ids[0]}",
        }
        ads.append(rec)

    ads.sort(key=lambda a: -a["metrics"]["spend"])
    for i, a in enumerate(ads, 1):
        a["spend_rank"] = i

    n_ren = sum(1 for a in ads if a["convention"] == "renamed")
    tot = sum(a["metrics"]["spend"] for a in ads) or 1
    s_ren = sum(a["metrics"]["spend"] for a in ads if a["convention"] == "renamed")
    s_tag = sum(a["metrics"]["spend"] for a in ads if a["convention"] != "untagged")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start": starts[0], "end": ends[-1]},
        "source": f"meta-ads-manager-export:{csv_path.name}",
        "shop": "18b070.myshopify.com",
        "has_nc": False,
        "attribution": {"primary": f"Meta Ads Manager · {attribution}",
                        "secondary": None, "window": attribution},
        "ad_accounts": [ACCOUNT],
        "scope_note": (
            f"Every ad that spent in {ACCOUNT}, {starts[0]} → {ends[-1]}, rolled up to "
            f"{len(ads):,} creatives. {n_ren:,} carry the new taxonomy in their name "
            f"({s_ren/tot:.0%} of spend); legacy names are tagged from the MASTER "
            f"workbook where possible ({s_tag/tot:.0%} of spend tagged in total) and "
            "otherwise show as (untagged). Revenue and ROAS are Meta-attributed; "
            "Triple Whale new-customer metrics are not in this export."
        ),
        "ad_count": len(ads),
        "ads": ads,
    }


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: build_from_meta_csv.py <meta export .csv> [MASTER .xlsx]")
    src = pathlib.Path(sys.argv[1])
    master = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else None
    root = pathlib.Path(__file__).resolve().parent.parent
    out = root / "public" / "data"
    (out / "history").mkdir(parents=True, exist_ok=True)

    m = build(src, master)
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
    for c in ("renamed", "master", "untagged"):
        s = sum(a["metrics"]["spend"] for a in ads if a["convention"] == c)
        print(f"  {c:<9} {sum(1 for a in ads if a['convention']==c):>5} creatives  ${s:>12,.0f}  {s/spend:5.1%}")
    print("dimension coverage (by spend):")
    for d in TAXONOMY + EXTRA + ["date"]:
        s = sum(a["metrics"]["spend"] for a in ads if a.get(d))
        print(f"  {d:<16} {s/spend:5.1%}")


if __name__ == "__main__":
    main()
