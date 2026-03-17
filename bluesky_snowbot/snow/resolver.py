"""
Resort/location resolver.

Maps a free-text resort name to a ResortLocation (lat/lon + state).

Resolution order:
  1. Exact or fuzzy match against the hardcoded RESORTS table (fast, offline)
  2. Nominatim geocoding fallback for unknown locations

The "sc_id" field in each _RESORTS entry is the SnoCountry numeric resort ID.
These are populated by running:
    python scripts/fetch_resort_list.py
after obtaining a SnoCountry API key. Resorts without a known sc_id will
fall back to Open-Meteo for all data.

Usage:
    from bluesky_snowbot.snow.resolver import SnowResolver
    loc = SnowResolver().resolve("Vail")        # → ResortLocation
    loc = SnowResolver().resolve("Park City")   # → ResortLocation
"""

from __future__ import annotations

import logging
from typing import Optional

from bluesky_snowbot.snow.models import ResortLocation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hardcoded resort table — name aliases, coords, state, elevation, SnoCountry ID
#
# "sc_id" is the SnoCountry numeric resort ID.
# Run `python scripts/fetch_resort_list.py` after getting your API key to
# populate these values. Resorts with sc_id=None fall back to Open-Meteo only.
# ---------------------------------------------------------------------------

_RESORTS: list[dict] = [
    # Colorado
    {"names": ["vail", "vail ski resort", "vail mountain"],
     "display": "Vail", "lat": 39.6433, "lon": -106.3781, "state": "CO", "elev": 3527, "sc_id": None},
    {"names": ["breckenridge", "breck", "breckenridge ski resort"],
     "display": "Breckenridge", "lat": 39.4817, "lon": -106.0384, "state": "CO", "elev": 3914, "sc_id": None},
    {"names": ["aspen", "aspen mountain", "aspen snowmass", "snowmass"],
     "display": "Aspen Snowmass", "lat": 39.2084, "lon": -106.9497, "state": "CO", "elev": 3651, "sc_id": None},
    {"names": ["telluride", "telluride ski resort"],
     "display": "Telluride", "lat": 37.9375, "lon": -107.8123, "state": "CO", "elev": 3832, "sc_id": None},
    {"names": ["steamboat", "steamboat springs", "steamboat ski resort"],
     "display": "Steamboat Springs", "lat": 40.4850, "lon": -106.8317, "state": "CO", "elev": 3221, "sc_id": None},
    {"names": ["copper", "copper mountain", "copper mountain resort"],
     "display": "Copper Mountain", "lat": 39.5021, "lon": -106.1498, "state": "CO", "elev": 3750, "sc_id": None},
    {"names": ["arapahoe basin", "a-basin", "abasin"],
     "display": "Arapahoe Basin", "lat": 39.6426, "lon": -105.8713, "state": "CO", "elev": 3979, "sc_id": None},
    {"names": ["keystone", "keystone resort", "keystone ski"],
     "display": "Keystone", "lat": 39.6047, "lon": -105.9577, "state": "CO", "elev": 3658, "sc_id": None},
    {"names": ["crested butte", "crested butte mountain resort"],
     "display": "Crested Butte", "lat": 38.9000, "lon": -106.9650, "state": "CO", "elev": 3707, "sc_id": None},
    {"names": ["winter park", "winter park resort"],
     "display": "Winter Park", "lat": 39.8868, "lon": -105.7625, "state": "CO", "elev": 2903, "sc_id": None},
    # Utah
    {"names": ["park city", "park city mountain", "park city resort", "pcmr"],
     "display": "Park City Mountain", "lat": 40.6514, "lon": -111.5079, "state": "UT", "elev": 3051, "sc_id": None},
    {"names": ["snowbird", "snowbird resort", "snowbird ski"],
     "display": "Snowbird", "lat": 40.5830, "lon": -111.6550, "state": "UT", "elev": 3353, "sc_id": None},
    {"names": ["alta", "alta ski area", "alta utah"],
     "display": "Alta", "lat": 40.5882, "lon": -111.6385, "state": "UT", "elev": 2601, "sc_id": None},
    {"names": ["deer valley", "deer valley resort"],
     "display": "Deer Valley", "lat": 40.6374, "lon": -111.4964, "state": "UT", "elev": 2917, "sc_id": None},
    {"names": ["canyons", "the canyons", "canyons resort"],
     "display": "Canyons Village", "lat": 40.6818, "lon": -111.5615, "state": "UT", "elev": 2088, "sc_id": None},
    {"names": ["solitude", "solitude mountain resort"],
     "display": "Solitude Mountain", "lat": 40.6199, "lon": -111.5919, "state": "UT", "elev": 3158, "sc_id": None},
    # California
    {"names": ["mammoth", "mammoth mountain", "mammoth ski"],
     "display": "Mammoth Mountain", "lat": 37.6308, "lon": -119.0326, "state": "CA", "elev": 3369, "sc_id": None},
    {"names": ["palisades tahoe", "squaw valley", "squaw", "palisades"],
     "display": "Palisades Tahoe", "lat": 39.1969, "lon": -120.2358, "state": "CA", "elev": 2335, "sc_id": None},
    {"names": ["northstar", "northstar california", "northstar tahoe"],
     "display": "Northstar California", "lat": 39.2741, "lon": -120.1212, "state": "CA", "elev": 2399, "sc_id": None},
    {"names": ["heavenly", "heavenly mountain resort", "heavenly tahoe"],
     "display": "Heavenly Mountain", "lat": 38.9352, "lon": -119.9400, "state": "CA", "elev": 3064, "sc_id": None},
    {"names": ["kirkwood", "kirkwood mountain resort"],
     "display": "Kirkwood", "lat": 38.6845, "lon": -120.0660, "state": "CA", "elev": 2377, "sc_id": None},
    # Wyoming
    {"names": ["jackson hole", "jackson", "jhmr", "jackson hole mountain resort"],
     "display": "Jackson Hole Mountain Resort", "lat": 43.5876, "lon": -110.8277, "state": "WY", "elev": 1924, "sc_id": None},
    # Montana
    {"names": ["big sky", "big sky resort", "big sky montana"],
     "display": "Big Sky Resort", "lat": 45.2599, "lon": -111.4008, "state": "MT", "elev": 2286, "sc_id": None},
    # Idaho
    {"names": ["sun valley", "sun valley resort"],
     "display": "Sun Valley", "lat": 43.6960, "lon": -114.3513, "state": "ID", "elev": 2780, "sc_id": None},
    # New Mexico
    {"names": ["taos", "taos ski valley"],
     "display": "Taos Ski Valley", "lat": 36.5953, "lon": -105.4411, "state": "NM", "elev": 3159, "sc_id": None},
    # Vermont
    {"names": ["stowe", "stowe mountain resort", "stowe vermont"],
     "display": "Stowe Mountain Resort", "lat": 44.5243, "lon": -72.7813, "state": "VT", "elev": 1339, "sc_id": None},
    {"names": ["killington", "killington resort", "killington vermont"],
     "display": "Killington Resort", "lat": 43.6045, "lon": -72.8201, "state": "VT", "elev": 1293, "sc_id": None},
    {"names": ["sugarbush", "sugarbush resort"],
     "display": "Sugarbush Resort", "lat": 44.1373, "lon": -72.9054, "state": "VT", "elev": 1201, "sc_id": None},
    # Canada
    {"names": ["whistler", "whistler blackcomb", "whistler bc"],
     "display": "Whistler Blackcomb", "lat": 50.1163, "lon": -122.9574, "state": "BC", "elev": 2182, "sc_id": None},
    {"names": ["banff", "lake louise", "lake louise ski", "banff sunshine"],
     "display": "Lake Louise Ski Resort", "lat": 51.4254, "lon": -116.1773, "state": "AB", "elev": 2637, "sc_id": None},
]

# Build a fast lookup map from canonical name strings
_LOOKUP: dict[str, dict] = {}
for _entry in _RESORTS:
    for _alias in _entry["names"]:
        _LOOKUP[_alias] = _entry


class SnowResolver:
    """Resolves free-text location input to a ResortLocation."""

    def resolve(self, raw: str) -> ResortLocation:
        """
        Resolve a raw location string to a ResortLocation.
        Raises ValueError if resolution fails completely.
        """
        normalised = raw.strip().lower()

        # 1. Exact match
        entry = _LOOKUP.get(normalised)
        if entry:
            return _entry_to_location(entry)

        # 2. Prefix / substring match
        for key, e in _LOOKUP.items():
            if normalised in key or key in normalised:
                logger.debug("[resolver] Fuzzy match %r → %r", normalised, e["display"])
                return _entry_to_location(e)

        # 3. Nominatim geocoding fallback
        loc = self._geocode_fallback(raw)
        if loc:
            return loc

        raise ValueError(f"Could not resolve location: {raw!r}")

    @staticmethod
    def _geocode_fallback(raw: str) -> Optional[ResortLocation]:
        """Use Nominatim to resolve an unknown location."""
        try:
            from geopy.geocoders import Nominatim
            geo = Nominatim(user_agent="bluesky-snowbot/1.0")
            result = geo.geocode(raw, timeout=10)
            if result is None:
                return None
            logger.debug("[resolver] Nominatim resolved %r → %s", raw, result.address)
            # Extract state code from address components if possible
            address = getattr(result, "raw", {}).get("address", {})
            state = (address.get("state_code") or address.get("state") or "")[:2].upper()
            return ResortLocation(
                name=result.address.split(",")[0].strip(),
                lat=result.latitude,
                lon=result.longitude,
                state=state,
                country=address.get("country_code", "US").upper(),
            )
        except ImportError:
            logger.warning("[resolver] geopy not installed; Nominatim fallback unavailable")
            return None
        except Exception as exc:
            logger.warning("[resolver] Nominatim fallback failed for %r: %s", raw, exc)
            return None


def _entry_to_location(entry: dict) -> ResortLocation:
    return ResortLocation(
        name=entry["display"],
        lat=entry["lat"],
        lon=entry["lon"],
        state=entry["state"],
        elevation_m=entry.get("elev"),
        snocountry_id=entry.get("sc_id"),
    )
