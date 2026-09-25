# Natural Rems — Creative Performance Dashboard

Ad-level creative performance for Natural Rems, backed by Triple Whale.
A port of the IM8 creative dashboard, rebuilt around Triple Whale data and
Natural Rems' ad-naming conventions.

**Live:** https://deniskims11.github.io/creative-dashboard/

## How it works

```
Triple Whale API ──► scripts/fetch_triplewhale.py ──► public/data/latest.json ──► React SPA
   (every 4h)          parse_ad_name.py                + history/*.json.gz
```

No backend. A GitHub Action refreshes the data every four hours and commits
it; GitHub Pages serves a static build that reads `latest.json` in the browser
and derives every view client-side.

## Naming conventions

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

Coverage on a real 84-ad sample: angle 85%, buying type 98%, concept 90%,
creative no. 82%.

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
| `TW_CLIENT_ID` | Triple Whale → Settings → API Keys (admin only) | data refresh |
| `TW_CLIENT_SECRET` | same | data refresh |

Until these exist the refresh job **no-ops with a notice** instead of failing,
so the repo does not sit red.

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
      empty manifest until they are.
- [ ] The API endpoint and auth flow in `fetch_triplewhale.py` mirror the
      request the Triple Whale app itself sends; **they have not been executed
      against a real API key yet** and may need adjustment on first run.
- [ ] Meta only. Triple Whale also carries TikTok and Google for this shop.

## Note on visibility

This repository is **public**, which means Natural Rems' spend and revenue are
readable by anyone with the URL. That was a deliberate choice to mirror the
IM8 setup. Making it private requires either a paid GitHub plan (for private
Pages) or moving hosting elsewhere.
