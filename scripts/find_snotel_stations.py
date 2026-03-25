"""
Find the nearest SNOTEL station for each resort in resolver.py.

Queries the NRCS AWDB REST API for all SNOTEL stations in each resort's
state, then ranks by distance and elevation similarity. Outputs a review
table to the console and writes scripts/snotel_stations.csv for inspection.

After reviewing, copy the snotel_triplet values into resolver.py.

Usage:
    python scripts/find_snotel_stations.py

    # Limit distance radius (default 30 miles):
    python scripts/find_snotel_stations.py --max-miles 20

Notes:
    - SNOTEL covers the western US and a few eastern states.
    - Canadian provinces (BC, AB) and NZ are skipped — different networks.
    - Vermont has very limited SNOTEL coverage; may return no match.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from bluesky_snowbot.snow.resolver import _RESORTS

AWDB_BASE    = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1"
TIMEOUT      = 30

# SNOTEL only covers these US states (skip CA, AB, NZ etc. if absent)
SNOTEL_STATES = {"CO", "UT", "CA", "WY", "MT", "ID", "NM", "VT", "MN", "WA", "OR", "NV", "AZ", "AK", "ME", "NH"}

# Skipped regions — different snow networks, handled separately
SKIP_REGIONS = {"BC", "AB", "NZ"}


# ---------------------------------------------------------------------------
# Haversine distance
# ---------------------------------------------------------------------------

def _miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3959.0  # Earth radius in miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# AWDB API
# ---------------------------------------------------------------------------

_ALL_SNTL_STATIONS: list[dict] = []   # fetched once, reused across states


def fetch_all_sntl_stations() -> list[dict]:
    """
    Fetch every active SNOTEL (SNTL) station in one request.
    The stateCode filter appears to be ignored by the API, so we fetch
    everything and filter client-side.
    """
    global _ALL_SNTL_STATIONS
    if _ALL_SNTL_STATIONS:
        return _ALL_SNTL_STATIONS

    url = f"{AWDB_BASE}/stations"
    params = {"networkCds": "SNTL", "returnObjects": "true"}
    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        all_stations = resp.json()
        # Keep only true SNTL stations that are still active
        _ALL_SNTL_STATIONS = [
            s for s in all_stations
            if s.get("networkCode") == "SNTL"
            and s.get("endDate", "")[:4] == "2100"
        ]
        print(f"Fetched {len(_ALL_SNTL_STATIONS)} active SNTL stations "
              f"({len(all_stations)} total across all networks)\n")
    except Exception as exc:
        print(f"ERROR fetching SNOTEL stations: {exc}")
    return _ALL_SNTL_STATIONS


def fetch_stations(state: str) -> list[dict]:
    """Return SNTL stations for a given state (filtered client-side)."""
    all_sntl = fetch_all_sntl_stations()
    state_stations = [s for s in all_sntl if s.get("stateCode") == state]
    print(f"  {state}: {len(state_stations)} SNTL stations")
    return state_stations


# ---------------------------------------------------------------------------
# Matching logic
# ---------------------------------------------------------------------------

def find_best_station(
    resort: dict,
    stations: list[dict],
    max_miles: float,
    max_elev_delta_ft: float = 3000,
) -> dict | None:
    """
    Return the best-matching SNOTEL station for a resort, or None.

    Scoring prefers:
      1. Distance (weighted 70%)
      2. Elevation similarity (weighted 30%)
    """
    resort_lat  = resort["lat"]
    resort_lon  = resort["lon"]
    resort_elev = (resort.get("elev") or 0) * 3.281  # m → ft

    candidates = []
    for s in stations:
        lat  = s.get("latitude")
        lon  = s.get("longitude")
        elev = s.get("elevation", 0)  # already in feet
        if lat is None or lon is None:
            continue
        dist = _miles(resort_lat, resort_lon, lat, lon)
        if dist > max_miles:
            continue
        elev_delta = abs(elev - resort_elev) if resort_elev else 0
        if resort_elev and elev_delta > max_elev_delta_ft:
            continue
        score = dist * 0.7 + (elev_delta / 1000) * 0.3
        candidates.append((score, dist, elev_delta, s))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0])
    score, dist, elev_delta, station = candidates[0]
    return {
        "triplet":    station["stationTriplet"],
        "name":       station["name"],
        "lat":        station["latitude"],
        "lon":        station["longitude"],
        "elev_ft":    int(station.get("elevation", 0)),
        "dist_miles": round(dist, 1),
        "elev_delta_ft": int(elev_delta),
        "score":      round(score, 2),
        "candidates": len(candidates),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Find nearest SNOTEL station per resort")
    parser.add_argument("--max-miles", type=float, default=50.0,
                        help="Maximum search radius in miles (default: 50)")
    args = parser.parse_args()

    # Collect unique states that SNOTEL covers
    states_needed = {
        r["state"] for r in _RESORTS
        if r["state"] not in SKIP_REGIONS and r["state"] in SNOTEL_STATES
    }
    skipped_regions = {r["state"] for r in _RESORTS if r["state"] in SKIP_REGIONS}

    print(f"\nFetching SNOTEL stations for: {', '.join(sorted(states_needed))}")
    if skipped_regions:
        print(f"Skipping (non-SNOTEL regions): {', '.join(sorted(skipped_regions))}\n")

    # Fetch station lists per state
    stations_by_state: dict[str, list[dict]] = {}
    for state in sorted(states_needed):
        stations_by_state[state] = fetch_stations(state)

    # Match each resort
    results = []
    print(f"\n{'Resort':<30} {'State':<5} {'Station':<30} {'Dist':>6} {'ΔElev':>7} {'Triplet'}")
    print("-" * 95)

    for resort in _RESORTS:
        state = resort["state"]
        if state in SKIP_REGIONS:
            print(f"{resort['display']:<30} {state:<5} {'(non-SNOTEL region)':<30}")
            results.append({
                "resort":          resort["display"],
                "state":           state,
                "resort_lat":      resort["lat"],
                "resort_lon":      resort["lon"],
                "resort_elev_m":   resort.get("elev", ""),
                "snotel_triplet":  "",
                "snotel_name":     "",
                "snotel_lat":      "",
                "snotel_lon":      "",
                "snotel_elev_ft":  "",
                "dist_miles":      "",
                "elev_delta_ft":   "",
                "candidates":      0,
                "notes":           "non-SNOTEL region",
            })
            continue

        if state not in stations_by_state:
            print(f"{resort['display']:<30} {state:<5} {'(state not fetched)':<30}")
            continue

        match = find_best_station(resort, stations_by_state[state], args.max_miles)

        if match:
            print(
                f"{resort['display']:<30} {state:<5} {match['name']:<30} "
                f"{match['dist_miles']:>5.1f}mi {match['elev_delta_ft']:>+6}ft  {match['triplet']}"
            )
            results.append({
                "resort":          resort["display"],
                "state":           state,
                "resort_lat":      resort["lat"],
                "resort_lon":      resort["lon"],
                "resort_elev_m":   resort.get("elev", ""),
                "snotel_triplet":  match["triplet"],
                "snotel_name":     match["name"],
                "snotel_lat":      match["lat"],
                "snotel_lon":      match["lon"],
                "snotel_elev_ft":  match["elev_ft"],
                "dist_miles":      match["dist_miles"],
                "elev_delta_ft":   match["elev_delta_ft"],
                "candidates":      match["candidates"],
                "notes":           "",
            })
        else:
            print(f"{resort['display']:<30} {state:<5} {'NO MATCH within'} {args.max_miles:.0f} miles")
            results.append({
                "resort":          resort["display"],
                "state":           state,
                "resort_lat":      resort["lat"],
                "resort_lon":      resort["lon"],
                "resort_elev_m":   resort.get("elev", ""),
                "snotel_triplet":  "",
                "snotel_name":     "",
                "snotel_lat":      "",
                "snotel_lon":      "",
                "snotel_elev_ft":  "",
                "dist_miles":      "",
                "elev_delta_ft":   "",
                "candidates":      0,
                "notes":           f"no match within {args.max_miles:.0f} mi",
            })

    # Write CSV
    out_path = Path(__file__).parent / "snotel_stations.csv"
    fieldnames = [
        "resort", "state", "resort_lat", "resort_lon", "resort_elev_m",
        "snotel_triplet", "snotel_name", "snotel_lat", "snotel_lon",
        "snotel_elev_ft", "dist_miles", "elev_delta_ft", "candidates", "notes",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    matched   = sum(1 for r in results if r["snotel_triplet"])
    unmatched = sum(1 for r in results if not r["snotel_triplet"] and not r["notes"].startswith("non"))
    skipped   = sum(1 for r in results if r["notes"].startswith("non"))

    print(f"\nResults: {matched} matched, {unmatched} unmatched, {skipped} skipped (non-SNOTEL)")
    print(f"Full results written to {out_path}")
    print("\nNext step: review the CSV, then copy snotel_triplet values into resolver.py")


if __name__ == "__main__":
    main()
