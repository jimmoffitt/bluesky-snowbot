"""
Generate a comprehensive resort reference table as CSV.

Reads the hardcoded _RESORTS table from resolver.py and writes
scripts/resort_table.csv with all available geo metadata.

Usage:
    python scripts/generate_resort_table.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bluesky_snowbot.snow.resolver import _RESORTS, snotel_quality, SNOTEL_ICON_GOOD, SNOTEL_ICON_MARGINAL, SNOTEL_ICON_NONE


FIELDNAMES = [
    "display",
    "aliases",
    "state",
    "country",
    "lat",
    "lon",
    "elev_m",
    "elev_ft",
    "postal_code",
    "sc_id",
    "snotel_triplet",
    "snotel_dist_mi",
    "snotel_quality",
    "snotel_icon",
]


def main() -> None:
    rows = []
    for r in _RESORTS:
        triplet  = r.get("snotel")
        dist_mi  = r.get("snotel_dist_mi")
        quality  = snotel_quality(dist_mi, triplet)
        icon_map = {"good": SNOTEL_ICON_GOOD, "marginal": SNOTEL_ICON_MARGINAL, "none": SNOTEL_ICON_NONE}
        elev_m   = r.get("elev")
        elev_ft  = int(elev_m * 3.281) if elev_m else ""
        rows.append({
            "display":        r["display"],
            "aliases":        " | ".join(r["names"]),
            "state":          r["state"],
            "country":        r.get("country", "US"),
            "lat":            r["lat"],
            "lon":            r["lon"],
            "elev_m":         elev_m or "",
            "elev_ft":        elev_ft,
            "postal_code":    r.get("postal_code", ""),
            "sc_id":          r.get("sc_id", ""),
            "snotel_triplet": triplet or "",
            "snotel_dist_mi": dist_mi if dist_mi is not None else "",
            "snotel_quality": quality,
            "snotel_icon":    icon_map[quality],
        })

    out_path = Path(__file__).parent / "resort_table.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    # Console summary table
    print(f"\n{'Resort':<35} {'St':<3} {'Ctry':<4} {'Elev ft':>7} {'Postal':<8} {'SNOTEL':<18} {'Dist':>5} {'Q'}")
    print("-" * 90)
    for r in rows:
        snotel_col = r["snotel_triplet"] if r["snotel_triplet"] else "—"
        dist_col   = f"{r['snotel_dist_mi']}mi" if r["snotel_dist_mi"] != "" else "—"
        print(
            f"{r['display']:<35} {r['state']:<3} {r['country']:<4} "
            f"{str(r['elev_ft']):>7} {r['postal_code']:<8} "
            f"{snotel_col:<18} {dist_col:>6}  {r['snotel_icon']}"
        )

    good     = sum(1 for r in rows if r["snotel_quality"] == "good")
    marginal = sum(1 for r in rows if r["snotel_quality"] == "marginal")
    none     = sum(1 for r in rows if r["snotel_quality"] == "none")
    print(f"\nTotal: {len(rows)} resorts  |  {SNOTEL_ICON_GOOD} good: {good}  {SNOTEL_ICON_MARGINAL} marginal: {marginal}  {SNOTEL_ICON_NONE} none: {none}")
    print(f"Written to {out_path}")


if __name__ == "__main__":
    main()
