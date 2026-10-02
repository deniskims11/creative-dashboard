#!/usr/bin/env python3
"""Build the dashboard manifest from the Natural Rems MASTER naming workbook.

This workbook is a reviewed naming artefact, not a raw export: every creative
already carries a nine-field taxonomy (Ad Number | Product | Angle | Sub-Angle |
Concept | ICP | Script | Format | Awareness Stage) that a human validated
against the video transcript. So unlike the Triple Whale path, nothing here is
inferred from the ad name — the dimensions are read straight off the columns.

Scope limits that the UI must surface honestly (from the workbook's Read Me):
  * 552 video/dynamic ads only. 108 static ads are deliberately excluded.
  * One ad account (act_944402037249956), not all four on the shop.
  * June 1 - September 20 2026, America/New_York, Triple Attribution, 7-day.
Rates computed over this population describe reviewed video creative, not the
whole account.
"""
import json
import gzip
import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from xlsx_read import read_sheet
from parse_ad_name import parse_campaign

SRC_SHEET = "xl/worksheets/sheet1.xml"
THUMB_CDN = "https://files.triplewhale.com/thumbnails/facebook-ads"


def num(v, default=0.0):
    if v in (None, "", "-"):
        return default
    try:
        return float(str(v).replace("$", "").replace(",", ""))
    except ValueError:
        return default


def text(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def build(xlsx_path: pathlib.Path) -> dict:
    rows = read_sheet(str(xlsx_path), SRC_SHEET)
    header = [str(h or "").strip() for h in rows[0]]
    idx = {name: i for i, name in enumerate(header)}

    def cell(r, name):
        i = idx.get(name)
        return r[i] if i is not None and i < len(r) else None

    ads = []
    for r in rows[1:]:
        prev_name = text(cell(r, "Previous Ad Name"))
        if not prev_name:
            continue
        campaign = text(cell(r, "Campaign"))
        adset = text(cell(r, "Ad Set"))
        camp = parse_campaign(campaign)

        spend = num(cell(r, "Spend USD"))
        nc_rev = num(cell(r, "NC Revenue USD"))
        nc_orders = num(cell(r, "NC Orders"))
        px_rev = num(cell(r, "Pixel Revenue USD"))
        px_orders = num(cell(r, "Pixel Orders"))

        ad_id = text(cell(r, "Ad ID"))
        account_id = text(cell(r, "Account ID")) or "act_944402037249956"

        naming = text(cell(r, "Naming Status"))
        reviewed = bool(text(cell(r, "New Ad Name")))

        metrics = {
            "spend": spend,
            # Pixel-attributed totals are the primary revenue lens here.
            "rev": px_rev,
            "txns": px_orders,
            "pixel_rev": px_rev,
            "pixel_txns": px_orders,
            "nc_rev": nc_rev,
            "nc_txns": nc_orders,
            # The workbook ships its own computed ratios; keep them rather than
            # recomputing, so figures tie out to the file people are reviewing.
            "ncroas": num(cell(r, "NCROAS"), None) if cell(r, "NCROAS") else None,
            "nccpa": num(cell(r, "NCCPA USD"), None) if cell(r, "NCCPA USD") else None,
            # Not present in this workbook — the UI renders these as em-dashes.
            "impressions": 0, "clicks": 0,
        }
        metrics["roas"] = round(px_rev / spend, 4) if spend else 0
        metrics["pixel_roas"] = metrics["roas"]
        metrics["aov"] = round(px_rev / px_orders, 2) if px_orders else None
        metrics["cpa"] = round(spend / px_orders, 4) if px_orders else None
        metrics["cpm"] = 0
        metrics["cpc"] = None
        metrics["ctr"] = None

        rec = {
            "ad_name": prev_name,
            "new_ad_name": text(cell(r, "New Ad Name")),
            "creative_no": text(cell(r, "Ad Number")),
            "product": text(cell(r, "Product")),
            "angle": text(cell(r, "Angle")),
            "sub_angle": text(cell(r, "Sub-Angle")),
            "concept": text(cell(r, "Concept")),
            "icp": text(cell(r, "ICP")),
            "script": text(cell(r, "Script")),
            "format": text(cell(r, "Format")),
            "awareness_stage": text(cell(r, "Awareness Stage")),
            "naming_status": naming,
            "reviewed": reviewed,
            "convention": "reviewed" if reviewed else "source-incomplete",
            "spend_rank": int(num(cell(r, "Spend Rank"), 0)) or None,
            "campaign": campaign,
            "adset": adset,
            "campaigns": [campaign] if campaign else [],
            "adsets": [adset] if adset else [],
            "buying_type": camp["buying_type"],
            "batch": camp["batch"],
            "attribution_window": camp["attribution_window"],
            "geo": camp["geo"],
            "launch_date": None,
            "date": camp["launch_date"],
            "transcript_status": text(cell(r, "Transcript Status")),
            "has_transcript": bool(text(cell(r, "Full Transcript"))),
            "meta_ad_ids": [ad_id] if ad_id else [],
            "metrics": metrics,
        }
        if rec["date"]:
            d = rec["date"]
            rec["launch_date"] = f"20{d[0:2]}-{d[2:4]}-{d[4:6]}"
        if ad_id:
            rec["thumbnail_candidates"] = [f"{THUMB_CDN}/{account_id}/{ad_id}.jpg"]
            rec["thumbnail_url"] = rec["thumbnail_candidates"][0]
            rec["preview_link"] = (
                "https://business.facebook.com/adsmanager/manage/ads"
                f"?selected_ad_ids={ad_id}"
            )
        ads.append(rec)

    now = datetime.now(timezone.utc)
    return {
        "generated_at": now.isoformat(),
        "period": {"start": "2026-06-01", "end": "2026-09-20"},
        "source": f"master-workbook:{xlsx_path.name}",
        "shop": "18b070.myshopify.com",
        "attribution": {
            "primary": "Triple Whale Pixel Joined · Triple Attribution · 7-day",
            "secondary": "New-customer (NCROAS / NCCPA)",
            "window": "7 days, purchase-date basis",
        },
        "ad_accounts": ["act_944402037249956"],
        "scope_note": (
            "552 reviewed video/dynamic ads from one ad account "
            "(act_944402037249956). 108 static ads are excluded by design, and "
            "the shop's other three ad accounts are not included. Rates describe "
            "this reviewed video population, not the whole account."
        ),
        "ad_count": len(ads),
        "ads": ads,
    }


def main():
    src = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if not src or not src.exists():
        sys.exit("usage: build_from_master_xlsx.py <path to MASTER .xlsx>")
    root = pathlib.Path(__file__).resolve().parent.parent
    out_dir = root / "public" / "data"
    (out_dir / "history").mkdir(parents=True, exist_ok=True)

    manifest = build(src)
    ads = manifest["ads"]
    print(f"{len(ads)} creatives")

    latest = out_dir / "latest.json"
    latest.write_text(json.dumps(manifest, separators=(",", ":"), default=str))
    print(f"Wrote {latest} ({latest.stat().st_size // 1024} KB)")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    with gzip.open(out_dir / "history" / f"{stamp}.json.gz", "wt") as f:
        json.dump(manifest, f, separators=(",", ":"), default=str)

    # Coverage report — what the dashboard can actually pivot on.
    dims = ["angle", "sub_angle", "concept", "icp", "script", "format",
            "awareness_stage", "product", "creative_no", "buying_type", "date"]
    n = len(ads)
    spend = sum(a["metrics"]["spend"] for a in ads)
    rev = sum(a["metrics"]["rev"] for a in ads)
    ncrev = sum(a["metrics"]["nc_rev"] for a in ads)
    print(f"\nspend ${spend:,.0f} · pixel rev ${rev:,.0f} (ROAS {rev/spend:.2f}x) "
          f"· NC rev ${ncrev:,.0f} (NCROAS {ncrev/spend:.2f}x)")
    print("\ndimension coverage:")
    for d in dims:
        c = sum(1 for a in ads if a.get(d))
        print(f"  {d:<18} {c:>3}/{n}  {c/n*100:>5.1f}%")
    zero = sum(1 for a in ads if a["metrics"]["spend"] == 0)
    print(f"\nzero-spend creatives: {zero}")
    print(f"reviewed names: {sum(1 for a in ads if a['reviewed'])}/{n}")


if __name__ == "__main__":
    main()
