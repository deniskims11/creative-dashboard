# Natural Rems — Creative Performance Dashboard

Ad-level creative performance for Natural Rems, backed by Triple Whale.
A port of the IM8 creative dashboard, rebuilt around Triple Whale data and
Natural Rems' ad-naming conventions.

**Live:** https://deniskims11.github.io/creative-dashboard/

## How it works

**Automatic daily refresh (Oct 2026).** `.github/workflows/deploy-pages.yml` runs
every day at 11:00 UTC (and on every push / manual "Run workflow"). It calls
`scripts/fetch_tw_sql.py`, which queries the Triple Whale SQL API
(`POST /api/v2/orcabase/api/sql`, header `x-api-key`) for the last 120 days ending
yesterday: per-ad daily rows, names/campaign/ad set + Meta-reported conversions,
and each ad's lifetime first spend day + thumbnail. Weekly and day-14 launch
figures are derived from the daily rows, then `build_from_meta_csv.py` builds the
manifest and Pages publishes it. Nothing is committed; legacy tags come from
`data/master_tags.json` (regenerate with
`python3 scripts/build_from_meta_csv.py --dump-master <MASTER.xlsx> data/master_tags.json`).
Run it locally with `TW_API_KEY=... python3 scripts/fetch_tw_sql.py`.

The sections below describe the earlier manual pipelines.

```
MASTER workbook ──► scripts/build_from_master_xlsx.py ─┐
  (reviewed naming, manual)                            ├─► public/data/latest.json ──► React SPA
Triple Whale API ──► scripts/fetch_triplewhale.py ─────┘      + history/*.json.gz
  (every 4h, once credentials exist)
```

**The dashboard currently runs on a Meta Ads Manager ad-level export**
(Oct 20 2026: Jun 20 – Oct 19, 6,753 ads → 4,106 creatives, $6.48M spend):

```bash
python3 scripts/build_from_meta_csv.py ~/Downloads/Natural-Rems-Account-Ads-<range>.csv \
    ~/Downloads/Natural_Rems_MASTER_Ad_Names_Spend_NCROAS_552.xlsx
```

Export settings: Ads tab, filter "Had delivery", columns = Ad name, Ad ID,
Campaign name, Ad set name, Amount spent, Purchases, Purchases conversion value,
Impressions, Link clicks (saved column preset "Copy of Performance and clicks").
Ads renamed to the pipe taxonomy (`Ad No | Product | Angle | Sub-Angle | Concept |
ICP | Script | Format | Awareness [| Strategist | Editor | Hook]`) are read off the
name; legacy names fall back to the MASTER workbook, else `(untagged)`. Revenue is
Meta-attributed, so the UI hides Triple Whale new-customer metrics (`has_nc: false`).

Previously it ran on the MASTER workbook (below). That
workbook is a reviewed naming artefact: a human classified every creative
against its video transcript, so the dimensions are read straight off columns
rather than inferred from ad names.

No backend. A GitHub Action refreshes the data every four hours and commits
it; GitHub Pages serves a static build that reads `latest.json` in the browser
and derives every view client-side.

## Current data: the MASTER workbook

`Natural_Rems_MASTER_Ad_Names_Spend_NCROAS_552.xlsx` carries 552 creatives with
a reviewed nine-field taxonomy — Ad Number | Product | Angle | Sub-Angle |
Concept | ICP | Script | Format | Awareness Stage — plus spend, NCROAS, NCCPA,
new-customer revenue/orders and pixel revenue/orders.

```bash
python3 scripts/build_from_master_xlsx.py ~/Downloads/Natural_Rems_MASTER_Ad_Names_Spend_NCROAS_552.xlsx
```

Coverage: 525/552 fully reviewed (95%), 27 source-incomplete rows keep their
performance data and show as `(untagged)`.

### Scope limits — surfaced in the UI, not buried here

- **552 video/dynamic ads only. 108 static ads are excluded by design.**
- **One ad account** (`act_944402037249956`) of the four on this shop.
- June 1 – September 20 2026, Triple Attribution, 7-day, purchase-date basis.

Win rates and any other ratio therefore describe *this reviewed video
population*, not the whole account. The dashboard shows a scope banner saying
so on every tab.

## Naming conventions (Triple Whale API path)

Natural Rems does not use one ad-naming convention — it has several prefix
families that accumulated over time (`NRYM23_…`, `YVS_MUCUS_099_ASIM_V1`,
`ADTOK_R6S9.1`, `C33 V5 _ C_Lukas E_Lukas`, `RW_240710_…`). A positional
parser like IM8's would mark most of the account unknown.

So the parser reads **three** fields together:

| From | Fields |
|---|---|
| Campaign name (pipe-delimited, the reliable part) | buying type, angle, batch, attribution window, geo, date |
| Ad set name | launch date corroboration |
| Ad name (prefix families) | family, creative no., format, concept, persona, editor, version, hook variant |

Coverage measured on 301 live Natural Rems creatives: angle 81%, buying type
97%, concept 88%, creative no. 75%, version 72%. About 25% of ad names fall
into no recognised family at all (`Post BFCM 15`, `jn13`, `Dr Sebi VSL 9 V4`)
— those surface as `(untagged)` rather than being guessed at.

**Launch date** comes from the first day a creative recorded spend, not from a
name token — only about a third of Natural Rems ads carry a date in the name.

## Differences from the IM8 dashboard

Three bugs in the original were fixed rather than ported:

1. **Header attribution rendered blank** — the front end read
   `attribution.primary`, the generator wrote `{model, window_days,
   accounting_mode}`. The manifest here actually contains `primary`.
2. **Date filtering silently dropped undated ads.** Mitigated at the source:
   launch date is derived from spend, so coverage is near total.
3. **Win-rate denominators counted never-spent creatives** (16.7% of IM8's
   ads), depressing every rate. Zero-spend creatives are now excluded from
   rate denominators.

Also changed: creative previews come from Triple Whale's own thumbnail CDN
(keyed by Meta ad id), so **no Meta access token is required**.

## Setup

### Required secrets

| Secret | Where | Purpose |
|---|---|---|
| `TW_API_KEY` | Triple Whale → API Keys (shop 18b070.myshopify.com), scopes Summary Page + Pixel Attribution read | daily refresh |

Without it the deploy publishes the committed `public/data/latest.json`.

### Local

```bash
npm install
npm run dev
```

Build data from a Creative Analysis CSV export instead of the API:

```bash
python3 scripts/fetch_triplewhale.py --from-csv ~/Downloads/export.csv
```

Check parser coverage against the fixture corpus:

```bash
python3 scripts/parse_ad_name.py --coverage
```

## Status / known gaps

- [ ] `TW_CLIENT_ID` / `TW_CLIENT_SECRET` not yet set — dashboard ships with an
      empty manifest until they are. Triple Whale's CSV export is broken for this
      account (opening the export dialog clears the table), and its Sheets export
      targets a Google account that is not the repo owner's, so the API key is the
      only viable path to a full dataset.
- [ ] The API endpoint and auth flow in `fetch_triplewhale.py` mirror the
      request the Triple Whale app itself sends; **they have not been executed
      against a real API key yet** and may need adjustment on first run.
- [ ] Meta only. Triple Whale also carries TikTok and Google for this shop.

## Note on visibility

This repository is **public**, which means Natural Rems' spend and revenue are
readable by anyone with the URL. That was a deliberate choice to mirror the
IM8 setup. Making it private requires either a paid GitHub plan (for private
Pages) or moving hosting elsewhere.
