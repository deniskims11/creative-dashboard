"""Natural Rems Meta ad-name parser.

Unlike IM8 — which uses one rigid positional convention — Natural Rems names
ads with several prefix families that accumulated over time. A positional
parser would mark most of the account "unknown", so this parser works
differently:

  * The **campaign** and **ad set** names are the reliable, structured part.
    They are pipe-delimited and consistently carry buying type, angle, batch
    and launch date:
        "CBO | Listicle | Joint Batch 15.07 (B-1) | 07/25/26"
        "ABO | Testing Campaign | All Angles | 7dc | 8/13/26"
    Angle, buying type, batch and date come from there.

  * The **ad name** carries the creative identity: family prefix, creative
    number, format, concept, persona, editor and version.
        NRYM23_LPRSUFFERER_VSL_FIRST16IN1-DUAL-TIMELINE_V3
        YVS_MUCUS_099_ASIM_V1      NRT103_FM_MANBOOBS_TH_V1
        ADTOK_R6S9.1               C33 V5 _ C_Lukas E_Lukas

Every key is always present (None when not applicable) so downstream pivoting
can assume a stable schema.
"""
import re
from typing import Optional

# --- Angle vocabulary ------------------------------------------------------
# Canonical angle names. Order matters for the keyword fallback: the first
# match wins, so more specific multi-word angles come first.
ANGLE_KEYWORDS = [
    ("BLOOD PRESSURE", ["blood pressure", "bloodpressure", "bp advert"]),
    ("BLOOD FLOW",     ["blood flow", "bloodflow"]),
    ("SEA MOSS",       ["sea moss", "seamoss", "sea_moss"]),
    ("JOINT",          ["joint", "knee", "hip", "cartilage", "arthritis"]),
    ("MUCUS",          ["mucus", "hvms", "gulag", "sebi"]),
    ("TESTO",          ["testo", "testosterone"]),
    ("HEART",          ["heart", "cholesterol", "cardio"]),
    ("KIDNEY",         ["kidney"]),
    ("THYROID",        ["thyroid"]),
    ("SUPERFOOD",      ["superfood"]),
    ("GUM",            ["gum"]),
    ("WEIGHT LOSS",    ["hloss", "weight loss", "weightloss"]),
]

# Short codes used inside ad names, mapped to the same canonical angles.
ANGLE_CODES = {
    "YM": "MUCUS", "M": "MUCUS", "MUCUS": "MUCUS",
    "J": "JOINT", "JOINT": "JOINT", "JN": "JOINT",
    "T": "TESTO", "YT": "TESTO", "TESTO": "TESTO",
    "BP": "BLOOD PRESSURE", "BF": "BLOOD FLOW",
    "K": "KIDNEY", "KIDNEY": "KIDNEY",
    "HEART": "HEART", "THYROID": "THYROID",
    "SUPERFOOD": "SUPERFOOD", "GUM": "GUM",
}

FORMAT_TOKENS = {
    "VSL": "VSL", "UGC": "UGC", "ANMT": "ANIMATION", "ANIM": "ANIMATION",
    "STA": "STATIC", "STATIC": "STATIC", "IMG": "STATIC",
    "VID": "VIDEO", "VIDEO": "VIDEO", "TH": "TALKING HEAD",
    "STORY": "STORY", "SWIPE": "SWIPE", "NATIVE": "NATIVE",
    "POD": "PODCAST", "VO": "VOICEOVER", "ORGANIC": "ORGANIC",
    "LISTICLE": "LISTICLE", "ADVERTORIAL": "ADVERTORIAL",
}

PERSONA_TOKENS = {
    "M": "MALE", "F": "FEMALE", "FM": "FEMALE", "WW": "WOMEN",
    "MB": "MALE", "WB": "WOMEN",
}

# Only names we have actually confirmed as editors/creators. Anything else
# stays in `concept` rather than being guessed into a person.
KNOWN_EDITORS = {"ASIM", "LUKAS", "ABDUL", "DRM", "SUMO", "XSG", "ITRN"}

BUYING_TYPES = {"CBO", "ABO", "ASC", "ICON"}

_SCHEMA_KEYS = (
    "ad_name", "campaign", "adset", "family", "convention", "angle",
    "creative_no", "format", "concept", "persona", "editor", "version",
    "hook_variant", "variant", "buying_type", "campaign_angle", "batch",
    "attribution_window", "geo", "launch_date", "date", "dedup_key",
)


def _blank() -> dict:
    return {k: None for k in _SCHEMA_KEYS}


def _clean(tok: Optional[str]) -> Optional[str]:
    if tok is None:
        return None
    t = str(tok).strip(" _-–")
    if not t or t.upper() in {"NA", "NULL", "NONE"}:
        return None
    return t


def _norm_date(raw: str) -> Optional[str]:
    """Normalise the date tokens found in campaign/ad set names to YYMMDD.

    Handles 7/2/26, 07/25/26, 09/01/2026, 26.05, 15.07, 8/13/26.
    Two-part forms (DD.MM) have no year and are rejected — a wrong year is
    worse than no date, since the whole monthly axis keys off this.
    """
    if not raw:
        return None
    raw = raw.strip()
    m = re.match(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})$", raw)
    if not m:
        return None
    a, b, y = m.groups()
    year = y[-2:]
    month, day = int(a), int(b)
    # Campaign dates are US-style M/D/YY.
    if month > 12:
        month, day = day, month
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year}{month:02d}{day:02d}"


def parse_campaign(campaign: Optional[str]) -> dict:
    """Pull buying type, angle, batch, attribution window, geo and date out of
    a pipe-delimited campaign name."""
    out = {
        "buying_type": None, "campaign_angle": None, "batch": None,
        "attribution_window": None, "geo": None, "launch_date": None,
    }
    if not campaign or not isinstance(campaign, str):
        return out
    parts = [p.strip() for p in campaign.split("|") if p.strip()]
    lowered = campaign.lower()

    for p in parts:
        up = p.upper()
        if out["buying_type"] is None:
            for bt in BUYING_TYPES:
                # Match as a standalone word so "ABO" doesn't fire on "ABOUT".
                if re.search(rf"\b{bt}\b", up):
                    out["buying_type"] = bt
                    break
        if out["attribution_window"] is None:
            m = re.search(r"\b(\d+)\s*DC\b", up)
            if m:
                out["attribution_window"] = f"{m.group(1)}DC"
        if out["geo"] is None and re.search(r"\b(USA|US|UK|CA)\b", up):
            out["geo"] = re.search(r"\b(USA|US|UK|CA)\b", up).group(1)
        if out["batch"] is None and "batch" in p.lower():
            out["batch"] = p
        if out["launch_date"] is None:
            m = re.search(r"\b(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})\b", p)
            if m:
                out["launch_date"] = _norm_date(m.group(1))

    # Angle: prefer an explicit "<X> Angle" phrase, else keyword scan.
    m = re.search(r"([A-Za-z][A-Za-z ]{2,20}?)\s+angle\b", lowered)
    if m:
        phrase = m.group(1).strip()
        for canon, keys in ANGLE_KEYWORDS:
            if any(k in phrase for k in keys):
                out["campaign_angle"] = canon
                break
        if out["campaign_angle"] is None and phrase not in {"all", "all angles"}:
            out["campaign_angle"] = phrase.upper()
    if out["campaign_angle"] is None:
        for canon, keys in ANGLE_KEYWORDS:
            if any(k in lowered for k in keys):
                out["campaign_angle"] = canon
                break
    return out


# --- Ad-name families ------------------------------------------------------
# Each entry: (family, compiled regex, handler). First match wins.
_FAMILIES = [
    ("NRYM",  re.compile(r"^NRYM(\d+)_", re.I),            {"angle": "MUCUS"}),
    ("NRYT",  re.compile(r"^NRYT(\d+)_", re.I),            {"angle": "TESTO"}),
    ("NRT",   re.compile(r"^NRT(\d+)_", re.I),             {"angle": "TESTO"}),
    ("NRJ",   re.compile(r"^NRJ(\d+)_", re.I),             {"angle": "JOINT"}),
    ("NRBP",  re.compile(r"^NRBP_?(\d+)_", re.I),          {"angle": "BLOOD PRESSURE"}),
    ("NRBF",  re.compile(r"^NRBF(\d+)_", re.I),            {"angle": "BLOOD FLOW"}),
    ("NCK",   re.compile(r"^NCK_M(\d+)", re.I),            {"angle": "MUCUS"}),
    ("ADTOK", re.compile(r"^ADTOK_R(\d+)S(\d+)", re.I),    {}),
    ("WNR",   re.compile(r"^WNR(\d+)_", re.I),             {}),
    ("WLD",   re.compile(r"^WLD_([A-Z]+)_(\d+)", re.I),    {"angle_from": 1}),
    ("YVS",   re.compile(r"^YVS_([A-Z]+)_(\d+)", re.I),    {"angle_from": 1}),
    ("YVS",   re.compile(r"^YVS_([A-Z])(\d+)_", re.I),     {"angle_from": 1}),
    ("YVS",   re.compile(r"^YVS_(\d+)_", re.I),            {}),
    ("HNY",   re.compile(r"^HNY_([A-Z]+)_?(\d+)", re.I),   {"angle": "KIDNEY"}),
    ("MTJ",   re.compile(r"^MTJ_(\w+?)_ID(\d+)", re.I),    {"angle_from": 1}),
    ("LUKAS", re.compile(r"^C(\d+)\s+V(\d+)\s*_\s*C_", re.I), {}),
    ("RW",    re.compile(r"^RW_(\d{6})_", re.I),           {}),
    ("AGENCY",re.compile(r"^NR_(\d{2}\.\d{2}\.\d{2})_", re.I), {}),
    ("CODE",  re.compile(r"^([A-Z]{1,3})(\d+)_", re.I),    {"angle_from": 1}),
    ("NUM",   re.compile(r"^(\d+)_", re.I),                {}),
]

_VERSION_RE = re.compile(r"[_\s-]V(\d+)\b", re.I)
_HOOK_RE = re.compile(r"[_\s-]H(\d+)\b", re.I)
_PSTATIC_RE = re.compile(r"\bP(\d+(?:\.\d+)?)\b")
_VARIANT_RE = re.compile(r"[-–]\s*(Copy(?:\s*\d+)?|Variation\s*\d+)\s*$", re.I)


def parse_ad_name(name: str, campaign: Optional[str] = None,
                  adset: Optional[str] = None) -> dict:
    """Parse an ad name (plus its campaign/ad set) into a structured dict."""
    result = _blank()
    result["ad_name"] = name
    result["campaign"] = campaign
    result["adset"] = adset

    camp = parse_campaign(campaign)
    result.update(camp)
    result["angle"] = camp["campaign_angle"]
    result["date"] = camp["launch_date"]

    if not isinstance(name, str) or not name.strip():
        result["convention"] = "unknown"
        return result

    working = name.strip()

    # 1. Trailing variant marker ("- Copy", "- Copy 2", "Variation 1").
    m = _VARIANT_RE.search(working)
    if m:
        result["variant"] = m.group(1).strip()
        working = working[: m.start()].strip()
    m = re.search(r"\bVariation\s*(\d+)\b", working, re.I)
    if m and not result["variant"]:
        result["variant"] = f"Variation {m.group(1)}"

    # 2. Version / hook-variant suffixes.
    m = _VERSION_RE.search(working)
    if m:
        result["version"] = f"V{int(m.group(1))}"
    m = _HOOK_RE.search(working)
    if m:
        result["hook_variant"] = f"H{int(m.group(1))}"

    # 3. Family prefix → creative number and (sometimes) angle.
    for family, rx, opts in _FAMILIES:
        m = rx.match(working)
        if not m:
            continue
        result["family"] = family
        result["convention"] = family
        groups = m.groups()
        if family == "ADTOK":
            result["creative_no"] = f"R{groups[0]}S{groups[1]}"
        elif family == "LUKAS":
            result["creative_no"] = f"C{groups[0]}"
            result["version"] = result["version"] or f"V{groups[1]}"
            e = re.search(r"E_(\w+)", working)
            if e and e.group(1).upper() in KNOWN_EDITORS:
                result["editor"] = e.group(1).upper()
        elif family in ("RW", "AGENCY"):
            result["creative_no"] = groups[0]
            if family == "RW" and re.match(r"^\d{6}$", groups[0]):
                result["date"] = result["date"] or groups[0]
        else:
            idx = opts.get("angle_from")
            if idx is not None and len(groups) >= idx:
                code = groups[idx - 1].upper()
                mapped = ANGLE_CODES.get(code)
                if mapped:
                    result["angle"] = result["angle"] or mapped
                result["creative_no"] = groups[idx] if len(groups) > idx else groups[0]
            else:
                result["creative_no"] = groups[0]
            if opts.get("angle"):
                result["angle"] = result["angle"] or opts["angle"]
        break
    else:
        result["convention"] = "unknown"

    # 4. Scan remaining tokens for format / persona / editor; whatever is left
    #    and looks descriptive becomes the concept.
    body = re.split(r"[_\s]+", working)
    concept_bits = []
    for tok in body[1:] if len(body) > 1 else []:
        t = tok.strip("()-–").upper()
        if not t:
            continue
        if t in FORMAT_TOKENS and result["format"] is None:
            result["format"] = FORMAT_TOKENS[t]
        elif t in KNOWN_EDITORS and result["editor"] is None:
            result["editor"] = t
        elif t in PERSONA_TOKENS and result["persona"] is None and len(t) <= 2:
            result["persona"] = PERSONA_TOKENS[t]
        elif re.fullmatch(r"V\d+|H\d+|C_\w+|E_\w+", t, re.I):
            continue
        elif re.fullmatch(r"[A-Z0-9\-]{3,}", t):
            concept_bits.append(t)
    if concept_bits:
        result["concept"] = "_".join(concept_bits[:3])

    # 5. Static-variant codes (Dr. RM Joint Native P8.9, MOF_Joint_P18).
    if result["version"] is None:
        m = _PSTATIC_RE.search(working)
        if m:
            result["version"] = f"P{m.group(1)}"

    # 6. Format fallback from the campaign (Listicle / Advertorial / Statics).
    if result["format"] is None and campaign:
        cl = campaign.lower()
        for key, canon in (("listicle", "LISTICLE"), ("advertorial", "ADVERTORIAL"),
                           ("static", "STATIC"), ("vsl", "VSL"), ("native", "NATIVE")):
            if key in cl:
                result["format"] = canon
                break

    # 7. Dedup key — name without variant/version noise, so "X", "X - Copy"
    #    and "X_V2" roll into one creative.
    dk = working
    dk = _VERSION_RE.sub("", dk)
    dk = _HOOK_RE.sub("", dk)
    result["dedup_key"] = dk.strip(" _-–") or working

    return result


if __name__ == "__main__":
    import json
    import pathlib
    import sys

    fixture = pathlib.Path(__file__).with_name("fixtures_ad_names.tsv")
    rows = []
    if fixture.exists():
        for line in fixture.read_text().splitlines():
            if not line.strip():
                continue
            cols = line.split("\t")
            rows.append((cols[0], cols[1] if len(cols) > 1 else None,
                         cols[2] if len(cols) > 2 else None))

    if "--coverage" in sys.argv:
        total = len(rows)
        filled = {k: 0 for k in _SCHEMA_KEYS}
        fams = {}
        for n, c, a in rows:
            r = parse_ad_name(n, c, a)
            fams[r["convention"]] = fams.get(r["convention"], 0) + 1
            for k, v in r.items():
                if v is not None:
                    filled[k] += 1
        print(f"{total} fixture ads\n")
        print("coverage by field:")
        for k in ("angle", "creative_no", "format", "concept", "version",
                  "persona", "editor", "buying_type", "batch", "date",
                  "attribution_window", "hook_variant"):
            print(f"  {k:<20} {filled[k]:>3}/{total}  {filled[k]/total*100:>5.1f}%")
        print("\nfamilies:")
        for f, n in sorted(fams.items(), key=lambda x: -x[1]):
            print(f"  {str(f):<10} {n}")
    else:
        for n, c, a in rows[:6]:
            print(f"\n--- {n}")
            print(json.dumps(parse_ad_name(n, c, a), indent=2))
