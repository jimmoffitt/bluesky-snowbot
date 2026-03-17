"""
Fetch the full SnoCountry resort list and print a mapping table.

Run this once after obtaining your SnoCountry API key to discover
the numeric IDs for each resort, then copy them into the sc_id fields
in bluesky_snowbot/snow/resolver.py.

Usage:
    SNOCOUNTRY_KEY=your_key python scripts/fetch_resort_list.py

    # Or with the sample key (returns up to 3 resorts for testing):
    SNOCOUNTRY_KEY=SnoCountry.example python scripts/fetch_resort_list.py

Output:
    Prints all resorts sorted by state, formatted for easy copy-paste
    into resolver.py. Also writes a full JSON dump to scripts/resort_list.json.
"""

import json
import os
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from bluesky_snowbot.snow.snocountry_client import SnoCountryClient


def main() -> None:
    api_key = os.environ.get("SNOCOUNTRY_KEY", "").strip()
    if not api_key:
        print("ERROR: Set SNOCOUNTRY_KEY environment variable before running.")
        print("  SNOCOUNTRY_KEY=your_key python scripts/fetch_resort_list.py")
        sys.exit(1)

    print(f"Fetching resort list with key: {api_key[:8]}...")
    client = SnoCountryClient(api_key)

    try:
        resorts = client.fetch_resort_list()
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    if not resorts:
        print("No resorts returned. Check your API key.")
        sys.exit(1)

    # Write full JSON dump
    out_path = Path(__file__).parent / "resort_list.json"
    with open(out_path, "w") as f:
        json.dump(resorts, f, indent=2)
    print(f"Full list written to {out_path}\n")

    # Print formatted table sorted by state then name
    alpine = [r for r in resorts if "Alpine" in str(r.get("resortType", ""))]
    alpine.sort(key=lambda r: (r.get("state", ""), r.get("resortName", "")))

    print(f"{'ID':>8}  {'State':<6}  {'Resort Name'}")
    print("-" * 60)
    current_state = None
    for r in alpine:
        state = r.get("state", "??")
        if state != current_state:
            if current_state is not None:
                print()
            current_state = state
        print(f"{r.get('id', '?'):>8}  {state:<6}  {r.get('resortName', '?')}")

    print(f"\nTotal alpine resorts: {len(alpine)}")
    print("\nCopy the IDs into bluesky_snowbot/snow/resolver.py — look for sc_id: None")


if __name__ == "__main__":
    main()
