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
import re
from typing import Optional

from bluesky_snowbot.snow.models import ResortLocation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SNOTEL coverage quality thresholds
# ---------------------------------------------------------------------------

SNOTEL_GOOD_MI     = 5.0    # Station is essentially on-mountain
SNOTEL_MARGINAL_MI = 15.0   # Neighboring basin — same general snowpack

# Quality icons shown in menus and posts
SNOTEL_ICON_GOOD     = "❄"   # Observed snow data (on-mountain station)
SNOTEL_ICON_MARGINAL = "≈"   # Nearby station — approximate
SNOTEL_ICON_NONE     = "○"   # Model estimate only


def snotel_quality(dist_mi: Optional[float], triplet: Optional[str]) -> str:
    """Return 'good', 'marginal', or 'none' based on station distance."""
    if triplet is None or dist_mi is None:
        return "none"
    if dist_mi <= SNOTEL_GOOD_MI:
        return "good"
    if dist_mi <= SNOTEL_MARGINAL_MI:
        return "marginal"
    return "none"


def snotel_icon(dist_mi: Optional[float], triplet: Optional[str]) -> str:
    """Return the display icon for the SNOTEL quality tier."""
    q = snotel_quality(dist_mi, triplet)
    return {
        "good":     SNOTEL_ICON_GOOD,
        "marginal": SNOTEL_ICON_MARGINAL,
        "none":     SNOTEL_ICON_NONE,
    }[q]


# ---------------------------------------------------------------------------
# Hardcoded resort table — name aliases, coords, state, elevation, SnoCountry ID
#
# Fields:
#   sc_id         — SnoCountry numeric resort ID (None = Open-Meteo only)
#   snotel        — NRCS AWDB station triplet (None = no nearby SNTL station)
#   snotel_dist_mi — Distance in miles from resort to SNOTEL station
#   postal_code   — ZIP/postal code for the resort area
# ---------------------------------------------------------------------------

_RESORTS: list[dict] = [
    # -------------------------------------------------------------------------
    # Colorado
    # -------------------------------------------------------------------------
    {"names": ["vail", "vail ski resort", "vail mountain"],
     "display": "Vail", "lat": 39.6400, "lon": -106.3742, "state": "CO", "elev": 3527,
     "postal_code": "81657", "sc_id": 303023,
     "snotel": "842:CO:SNTL", "snotel_dist_mi": 1.6},    # Vail Mountain
    {"names": ["breckenridge", "breck", "breckenridge ski resort"],
     "display": "Breckenridge", "lat": 39.4817, "lon": -106.0384, "state": "CO", "elev": 3914,
     "postal_code": "80424", "sc_id": 303007,
     "snotel": "415:CO:SNTL", "snotel_dist_mi": 7.1},    # Copper Mountain (nearest)
    {"names": ["aspen", "aspen mountain"],
     "display": "Aspen Mountain", "lat": 39.1911, "lon": -106.8175, "state": "CO", "elev": 3651,
     "postal_code": "81611", "sc_id": 303003,
     "snotel": "1101:CO:SNTL", "snotel_dist_mi": 11.2},  # Chapman Tunnel
    {"names": ["snowmass", "aspen snowmass", "snowmass village"],
     "display": "Snowmass", "lat": 39.2084, "lon": -106.9497, "state": "CO", "elev": 3650,
     "postal_code": "81615", "sc_id": 303020,
     "snotel": "669:CO:SNTL", "snotel_dist_mi": 13.8},   # North Lost Trail
    {"names": ["beaver creek", "beaver creek resort"],
     "display": "Beaver Creek", "lat": 39.6042, "lon": -106.5167, "state": "CO", "elev": 3488,
     "postal_code": "81620", "sc_id": 303005,
     "snotel": "1041:CO:SNTL", "snotel_dist_mi": 0.5},   # Beaver Ck Village
    {"names": ["telluride", "telluride ski resort"],
     "display": "Telluride", "lat": 37.9375, "lon": -107.8123, "state": "CO", "elev": 3832,
     "postal_code": "81435", "sc_id": 303022,
     "snotel": "1344:CO:SNTL", "snotel_dist_mi": 3.8},   # Alta Lakes
    {"names": ["steamboat", "steamboat springs", "steamboat ski resort"],
     "display": "Steamboat Springs", "lat": 40.4850, "lon": -106.8317, "state": "CO", "elev": 3221,
     "postal_code": "80487", "sc_id": 303021,
     "snotel": "457:CO:SNTL", "snotel_dist_mi": 4.3},    # Dry Lake
    {"names": ["copper", "copper mountain", "copper mountain resort"],
     "display": "Copper Mountain", "lat": 39.5014, "lon": -106.1516, "state": "CO", "elev": 3750,
     "postal_code": "80443", "sc_id": 303009,
     "snotel": "415:CO:SNTL", "snotel_dist_mi": 1.4},    # Copper Mountain
    {"names": ["arapahoe basin", "a-basin", "abasin"],
     "display": "Arapahoe Basin", "lat": 39.6423, "lon": -105.8717, "state": "CO", "elev": 3979,
     "postal_code": "80435", "sc_id": 303001,
     "snotel": "505:CO:SNTL", "snotel_dist_mi": 0.3},    # Grizzly Peak
    {"names": ["keystone", "keystone resort", "keystone ski"],
     "display": "Keystone", "lat": 39.5792, "lon": -105.9347, "state": "CO", "elev": 3658,
     "postal_code": "80435", "sc_id": 303014,
     "snotel": "505:CO:SNTL", "snotel_dist_mi": 5.8},    # Grizzly Peak
    {"names": ["crested butte", "crested butte mountain resort"],
     "display": "Crested Butte", "lat": 38.8697, "lon": -106.9878, "state": "CO", "elev": 3707,
     "postal_code": "81224", "sc_id": 303010,
     "snotel": "380:CO:SNTL", "snotel_dist_mi": 2.5},    # Butte
    {"names": ["eldora", "eldora mountain resort"],
     "display": "Eldora", "lat": 39.9486, "lon": -105.5639, "state": "CO", "elev": 3252,
     "postal_code": "80466", "sc_id": 303011,
     "snotel": "564:CO:SNTL", "snotel_dist_mi": 1.6},    # Lake Eldora
    {"names": ["loveland", "loveland ski area"],
     "display": "Loveland", "lat": 39.6814, "lon": -105.9016, "state": "CO", "elev": 4080,
     "postal_code": "80444", "sc_id": 303015,
     "snotel": "602:CO:SNTL", "snotel_dist_mi": 0.5},    # Loveland Basin
    {"names": ["winter park", "winter park resort"],
     "display": "Winter Park", "lat": 39.8917, "lon": -105.7631, "state": "CO", "elev": 2903,
     "postal_code": "80482", "sc_id": 303024,
     "snotel": "1186:CO:SNTL", "snotel_dist_mi": 5.8},   # Fool Creek
    {"names": ["wolf creek", "wolf creek ski area"],
     "display": "Wolf Creek", "lat": 37.4827, "lon": -106.8021, "state": "CO", "elev": 3353,
     "postal_code": "81154", "sc_id": 303025,
     "snotel": "874:CO:SNTL", "snotel_dist_mi": 0.3},    # Wolf Creek Summit
    # -------------------------------------------------------------------------
    # Utah
    # -------------------------------------------------------------------------
    {"names": ["park city", "park city mountain", "park city resort", "pcmr"],
     "display": "Park City Mountain", "lat": 40.6514, "lon": -111.5079, "state": "UT", "elev": 3051,
     "postal_code": "84060", "sc_id": None,
     "snotel": "814:UT:SNTL", "snotel_dist_mi": 2.3},    # Thaynes Canyon
    {"names": ["snowbird", "snowbird resort", "snowbird ski"],
     "display": "Snowbird", "lat": 40.5830, "lon": -111.6550, "state": "UT", "elev": 3353,
     "postal_code": "84092", "sc_id": None,
     "snotel": "766:UT:SNTL", "snotel_dist_mi": 1.0},    # Snowbird
    {"names": ["alta", "alta ski area", "alta utah"],
     "display": "Alta", "lat": 40.5882, "lon": -111.6385, "state": "UT", "elev": 2601,
     "postal_code": "84092", "sc_id": None,
     "snotel": "1308:UT:SNTL", "snotel_dist_mi": 0.2},   # Atwater
    {"names": ["deer valley", "deer valley resort"],
     "display": "Deer Valley", "lat": 40.6374, "lon": -111.4964, "state": "UT", "elev": 2917,
     "postal_code": "84060", "sc_id": None,
     "snotel": "814:UT:SNTL", "snotel_dist_mi": 2.2},    # Thaynes Canyon
    {"names": ["canyons", "the canyons", "canyons resort"],
     "display": "Canyons Village", "lat": 40.6818, "lon": -111.5615, "state": "UT", "elev": 2088,
     "postal_code": "84098", "sc_id": None,
     "snotel": "856:UT:SNTL", "snotel_dist_mi": 2.7},    # Parleys Upper
    {"names": ["solitude", "solitude mountain resort"],
     "display": "Solitude Mountain", "lat": 40.6199, "lon": -111.5919, "state": "UT", "elev": 3158,
     "postal_code": "84121", "sc_id": None,
     "snotel": "366:UT:SNTL", "snotel_dist_mi": 1.5},    # Brighton
    # -------------------------------------------------------------------------
    # California
    # -------------------------------------------------------------------------
    {"names": ["mammoth", "mammoth mountain", "mammoth ski"],
     "display": "Mammoth Mountain", "lat": 37.6308, "lon": -119.0326, "state": "CA", "elev": 3369,
     "postal_code": "93546", "sc_id": None,
     "snotel": "MHP:CA:MSNT", "snotel_dist_mi": 1.4},    # Mammoth Pass (MSNT network)
    {"names": ["palisades tahoe", "squaw valley", "squaw", "palisades"],
     "display": "Palisades Tahoe", "lat": 39.1969, "lon": -120.2358, "state": "CA", "elev": 2335,
     "postal_code": "96146", "sc_id": 916011,
     "snotel": "784:CA:SNTL", "snotel_dist_mi": 1.7},    # Palisades Tahoe
    {"names": ["northstar", "northstar california", "northstar tahoe"],
     "display": "Northstar California", "lat": 39.2741, "lon": -120.1212, "state": "CA", "elev": 2399,
     "postal_code": "96161", "sc_id": None,
     "snotel": "834:CA:SNTL", "snotel_dist_mi": 3.8},    # Truckee #2
    {"names": ["heavenly", "heavenly mountain resort", "heavenly tahoe"],
     "display": "Heavenly Mountain", "lat": 38.9352, "lon": -119.9400, "state": "CA", "elev": 3064,
     "postal_code": "96150", "sc_id": None,
     "snotel": "518:CA:SNTL", "snotel_dist_mi": 1.5},    # Heavenly Valley
    {"names": ["kirkwood", "kirkwood mountain resort"],
     "display": "Kirkwood", "lat": 38.6845, "lon": -120.0660, "state": "CA", "elev": 2377,
     "postal_code": "95646", "sc_id": None,
     "snotel": "1067:CA:SNTL", "snotel_dist_mi": 3.5},   # Carson Pass
    # -------------------------------------------------------------------------
    # Wyoming
    # -------------------------------------------------------------------------
    {"names": ["jackson hole", "jackson", "jhmr", "jackson hole mountain resort"],
     "display": "Jackson Hole Mountain Resort", "lat": 43.5876, "lon": -110.8277, "state": "WY", "elev": 1924,
     "postal_code": "83025", "sc_id": None,
     "snotel": "689:WY:SNTL", "snotel_dist_mi": 6.5},    # Phillips Bench
    # -------------------------------------------------------------------------
    # Montana
    # -------------------------------------------------------------------------
    {"names": ["big sky", "big sky resort", "big sky montana"],
     "display": "Big Sky Resort", "lat": 45.2599, "lon": -111.4008, "state": "MT", "elev": 2286,
     "postal_code": "59716", "sc_id": None,
     "snotel": "590:MT:SNTL", "snotel_dist_mi": 1.6},    # Lone Mountain
    # -------------------------------------------------------------------------
    # Idaho
    # -------------------------------------------------------------------------
    {"names": ["sun valley", "sun valley resort"],
     "display": "Sun Valley", "lat": 43.6960, "lon": -114.3513, "state": "ID", "elev": 2780,
     "postal_code": "83353", "sc_id": None,
     "snotel": "895:ID:SNTL", "snotel_dist_mi": 6.0},    # Chocolate Gulch
    # -------------------------------------------------------------------------
    # New Mexico
    # -------------------------------------------------------------------------
    {"names": ["taos", "taos ski valley"],
     "display": "Taos Ski Valley", "lat": 36.5953, "lon": -105.4411, "state": "NM", "elev": 3159,
     "postal_code": "87525", "sc_id": None,
     "snotel": "1168:NM:SNTL", "snotel_dist_mi": 1.2},   # Taos Powderhorn
    # -------------------------------------------------------------------------
    # Vermont — no SNTL stations; Open-Meteo only
    # -------------------------------------------------------------------------
    {"names": ["stowe", "stowe mountain resort", "stowe vermont"],
     "display": "Stowe Mountain Resort", "lat": 44.5243, "lon": -72.7813, "state": "VT", "elev": 1339,
     "postal_code": "05672", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["killington", "killington resort", "killington vermont"],
     "display": "Killington Resort", "lat": 43.6045, "lon": -72.8201, "state": "VT", "elev": 1293,
     "postal_code": "05751", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["sugarbush", "sugarbush resort"],
     "display": "Sugarbush Resort", "lat": 44.1373, "lon": -72.9054, "state": "VT", "elev": 1201,
     "postal_code": "05674", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    # -------------------------------------------------------------------------
    # Minnesota — no SNTL stations; Open-Meteo only
    # -------------------------------------------------------------------------
    {"names": ["afton alps", "afton alps ski"],
     "display": "Afton Alps", "lat": 44.8545, "lon": -92.7907, "state": "MN", "elev": 282,
     "postal_code": "55001", "sc_id": 612002, "snotel": None, "snotel_dist_mi": None},
    # -------------------------------------------------------------------------
    # -------------------------------------------------------------------------
    # Oregon
    # -------------------------------------------------------------------------
    {"names": ["mt bachelor", "mount bachelor", "bachelor"],
     "display": "Mt. Bachelor", "lat": 43.9743, "lon": -121.6864, "state": "OR", "elev": 2195,
     "postal_code": "97702", "sc_id": None,
     "snotel": "815:OR:SNTL", "snotel_dist_mi": 12.0},  # Three Creeks Meadow
    # -------------------------------------------------------------------------
    # Washington
    # -------------------------------------------------------------------------
    {"names": ["crystal mountain", "crystal mountain washington"],
     "display": "Crystal Mountain", "lat": 46.9354, "lon": -121.4748, "state": "WA", "elev": 1341,
     "postal_code": "98022", "sc_id": None,
     "snotel": "642:WA:SNTL", "snotel_dist_mi": 2.1},  # Morse Lake
    {"names": ["stevens pass"],
     "display": "Stevens Pass", "lat": 47.7462, "lon": -121.0859, "state": "WA", "elev": 1238,
     "postal_code": "98826", "sc_id": None,
     "snotel": "791:WA:SNTL", "snotel_dist_mi": 0.3},  # Stevens Pass
    {"names": ["mt baker", "mount baker", "baker"],
     "display": "Mt. Baker", "lat": 48.8550, "lon": -121.6730, "state": "WA", "elev": 1067,
     "postal_code": "98244", "sc_id": None,
     "snotel": "909:WA:SNTL", "snotel_dist_mi": 5.4},  # Wells Creek
    # -------------------------------------------------------------------------
    # Alaska
    # -------------------------------------------------------------------------
    {"names": ["alyeska", "alyeska resort"],
     "display": "Alyeska Resort", "lat": 60.9586, "lon": -149.1107, "state": "AK", "elev": 250,
     "postal_code": "99587", "sc_id": None,
     "snotel": "1103:AK:SNTL", "snotel_dist_mi": 0.7},  # Mt. Alyeska
    # -------------------------------------------------------------------------
    # Nevada
    # -------------------------------------------------------------------------
    {"names": ["mt rose", "mount rose", "mt rose ski tahoe"],
     "display": "Mt. Rose", "lat": 39.3144, "lon": -119.8903, "state": "NV", "elev": 2638,
     "postal_code": "89511", "sc_id": None,
     "snotel": "652:NV:SNTL", "snotel_dist_mi": 0.3},  # Mt Rose Ski Area
    # -------------------------------------------------------------------------
    # Utah additions
    # -------------------------------------------------------------------------
    {"names": ["brighton", "brighton resort", "brighton utah"],
     "display": "Brighton Resort", "lat": 40.5986, "lon": -111.5831, "state": "UT", "elev": 2667,
     "postal_code": "84121", "sc_id": None,
     "snotel": "366:UT:SNTL", "snotel_dist_mi": 0.1},  # Brighton
    {"names": ["snowbasin", "snowbasin resort"],
     "display": "Snowbasin Resort", "lat": 41.2119, "lon": -111.8519, "state": "UT", "elev": 1924,
     "postal_code": "84317", "sc_id": None,
     "snotel": "333:UT:SNTL", "snotel_dist_mi": 12.3},  # Ben Lomond Trail
    # -------------------------------------------------------------------------
    # Wyoming addition
    # -------------------------------------------------------------------------
    {"names": ["grand targhee", "grand targhee resort"],
     "display": "Grand Targhee Resort", "lat": 43.7888, "lon": -110.9577, "state": "WY", "elev": 2392,
     "postal_code": "83414", "sc_id": None,
     "snotel": "1082:WY:SNTL", "snotel_dist_mi": 1.6},  # Grand Targhee
    # -------------------------------------------------------------------------
    # Montana addition
    # -------------------------------------------------------------------------
    {"names": ["whitefish", "whitefish mountain", "whitefish mountain resort"],
     "display": "Whitefish Mountain Resort", "lat": 48.4799, "lon": -114.3558, "state": "MT", "elev": 2152,
     "postal_code": "59937", "sc_id": None,
     "snotel": "1311:MT:SNTL", "snotel_dist_mi": 19.7},  # Stryker Basin (marginal — 19.7mi)
    # -------------------------------------------------------------------------
    # Maine
    # -------------------------------------------------------------------------
    {"names": ["sunday river", "sunday river resort"],
     "display": "Sunday River", "lat": 44.4683, "lon": -70.8567, "state": "ME", "elev": 744,
     "postal_code": "04261", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["sugarloaf", "sugarloaf mountain", "sugarloaf resort"],
     "display": "Sugarloaf", "lat": 45.0316, "lon": -70.3151, "state": "ME", "elev": 1291,
     "postal_code": "04947", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    # -------------------------------------------------------------------------
    # New Hampshire
    # -------------------------------------------------------------------------
    {"names": ["loon mountain", "loon mountain resort"],
     "display": "Loon Mountain", "lat": 44.0312, "lon": -71.6236, "state": "NH", "elev": 966,
     "postal_code": "03251", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["cannon mountain", "cannon"],
     "display": "Cannon Mountain", "lat": 44.1562, "lon": -71.7028, "state": "NH", "elev": 1262,
     "postal_code": "03580", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    # -------------------------------------------------------------------------
    # Vermont addition
    # -------------------------------------------------------------------------
    {"names": ["jay peak", "jay peak resort"],
     "display": "Jay Peak", "lat": 44.9259, "lon": -72.5213, "state": "VT", "elev": 1272,
     "postal_code": "05859", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    # =========================================================================
    # Canada — Open-Meteo only (non-SNOTEL regions)
    # =========================================================================
    # British Columbia
    {"names": ["whistler", "whistler blackcomb", "whistler bc"],
     "display": "Whistler Blackcomb", "lat": 50.1150, "lon": -122.9500, "state": "BC", "country": "CA", "elev": 1100,
     "postal_code": "V0N 0A0", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["revelstoke", "revelstoke mountain resort"],
     "display": "Revelstoke Mountain Resort", "lat": 50.9583, "lon": -118.1638, "state": "BC", "country": "CA", "elev": 511,
     "postal_code": "V0E 2S0", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["big white", "big white ski resort"],
     "display": "Big White Ski Resort", "lat": 49.7216, "lon": -118.9266, "state": "BC", "country": "CA", "elev": 1755,
     "postal_code": "V1P 1P3", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["sun peaks", "sun peaks resort"],
     "display": "Sun Peaks Resort", "lat": 50.8746, "lon": -119.9256, "state": "BC", "country": "CA", "elev": 1255,
     "postal_code": "V0E 5N0", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["fernie", "fernie alpine resort"],
     "display": "Fernie Alpine Resort", "lat": 49.4639, "lon": -115.0866, "state": "BC", "country": "CA", "elev": 1052,
     "postal_code": "V0B 1M6", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["kicking horse", "kicking horse mountain resort"],
     "display": "Kicking Horse Mountain Resort", "lat": 51.2976, "lon": -117.0483, "state": "BC", "country": "CA", "elev": 1190,
     "postal_code": "V0A 1H0", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["red mountain", "red mountain resort"],
     "display": "Red Mountain Resort", "lat": 49.1017, "lon": -117.8188, "state": "BC", "country": "CA", "elev": 1161,
     "postal_code": "V0G 1Y0", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["panorama", "panorama mountain resort"],
     "display": "Panorama Mountain Resort", "lat": 50.4603, "lon": -116.2403, "state": "BC", "country": "CA", "elev": 1150,
     "postal_code": "V0A 1T0", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    # Alberta
    {"names": ["lake louise", "lake louise ski", "lake louise ski resort"],
     "display": "Lake Louise Ski Resort", "lat": 51.4419, "lon": -116.1622, "state": "AB", "country": "CA", "elev": 1645,
     "postal_code": "T0L 1E0", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["banff sunshine", "sunshine village", "banff sunshine village"],
     "display": "Banff Sunshine Village", "lat": 51.1150, "lon": -115.7641, "state": "AB", "country": "CA", "elev": 1660,
     "postal_code": "T1L 1J5", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["marmot basin", "marmot basin jasper"],
     "display": "Marmot Basin", "lat": 52.8011, "lon": -118.0832, "state": "AB", "country": "CA", "elev": 1698,
     "postal_code": "T0E 1E0", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    # Québec
    {"names": ["mont-tremblant", "tremblant"],
     "display": "Mont-Tremblant", "lat": 46.1167, "lon": -74.5999, "state": "QC", "country": "CA", "elev": 211,
     "postal_code": "J8E 1T1", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    # =========================================================================
    # Japan — Open-Meteo only
    # =========================================================================
    # Hokkaido
    {"names": ["niseko", "niseko grand hirafu", "niseko hirafu"],
     "display": "Niseko Grand Hirafu", "lat": 42.8635, "lon": 140.6981, "state": "HK", "country": "JP", "elev": 613,
     "postal_code": "044-0080", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["rusutsu", "rusutsu resort"],
     "display": "Rusutsu Resort", "lat": 42.7500, "lon": 140.9000, "state": "HK", "country": "JP", "elev": 715,
     "postal_code": "048-1503", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["furano", "furano ski resort"],
     "display": "Furano Ski Resort", "lat": 43.3427, "lon": 142.3840, "state": "HK", "country": "JP", "elev": 659,
     "postal_code": "076-8511", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["kiroro", "kiroro mountain resort"],
     "display": "Kiroro Mountain Resort", "lat": 43.0701, "lon": 140.9891, "state": "HK", "country": "JP", "elev": 850,
     "postal_code": "046-0571", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    # Nagano
    {"names": ["hakuba", "happo-one", "hakuba valley", "hakuba happo-one"],
     "display": "Hakuba Valley (Happo-One)", "lat": 36.7201, "lon": 137.8525, "state": "NG", "country": "JP", "elev": 900,
     "postal_code": "399-9301", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["nozawa onsen", "nozawa"],
     "display": "Nozawa Onsen", "lat": 36.9100, "lon": 138.3900, "state": "NG", "country": "JP", "elev": 823,
     "postal_code": "389-2502", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["shiga kogen", "shiga"],
     "display": "Shiga Kogen", "lat": 36.7057, "lon": 138.5079, "state": "NG", "country": "JP", "elev": 1300,
     "postal_code": "381-0401", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    # Niigata
    {"names": ["naeba", "naeba ski resort"],
     "display": "Naeba Ski Resort", "lat": 36.7896, "lon": 138.7837, "state": "NI", "country": "JP", "elev": 933,
     "postal_code": "949-6292", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    # =========================================================================
    # Europe — Open-Meteo only
    # =========================================================================
    # France
    {"names": ["chamonix", "chamonix mont-blanc"],
     "display": "Chamonix Mont-Blanc", "lat": 45.9237, "lon": 6.8694, "state": "FR", "country": "FR", "elev": 1036,
     "postal_code": "74400", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["courchevel", "courchevel 1850"],
     "display": "Courchevel 1850", "lat": 45.3941, "lon": 6.6150, "state": "FR", "country": "FR", "elev": 1850,
     "postal_code": "73120", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["val d'isere", "val d isere", "val disere"],
     "display": "Val d'Isère", "lat": 45.4475, "lon": 6.9792, "state": "FR", "country": "FR", "elev": 1840,
     "postal_code": "73150", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["meribel", "méribel"],
     "display": "Méribel", "lat": 45.3968, "lon": 6.5666, "state": "FR", "country": "FR", "elev": 1400,
     "postal_code": "73550", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    # Switzerland
    {"names": ["zermatt"],
     "display": "Zermatt", "lat": 46.0207, "lon": 7.7491, "state": "CH", "country": "CH", "elev": 1620,
     "postal_code": "3920", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["verbier"],
     "display": "Verbier", "lat": 46.1002, "lon": 7.2265, "state": "CH", "country": "CH", "elev": 1515,
     "postal_code": "1936", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["davos", "davos klosters"],
     "display": "Davos", "lat": 46.8021, "lon": 9.8335, "state": "CH", "country": "CH", "elev": 1560,
     "postal_code": "7260", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    # Austria
    {"names": ["st anton", "st. anton", "saint anton", "st anton am arlberg"],
     "display": "St. Anton am Arlberg", "lat": 47.1296, "lon": 10.2682, "state": "AT", "country": "AT", "elev": 1304,
     "postal_code": "6580", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["ischgl"],
     "display": "Ischgl", "lat": 47.0120, "lon": 10.2913, "state": "AT", "country": "AT", "elev": 1377,
     "postal_code": "6561", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["kitzbuhel", "kitzbühel"],
     "display": "Kitzbühel", "lat": 47.4464, "lon": 12.3922, "state": "AT", "country": "AT", "elev": 762,
     "postal_code": "6370", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["lech", "lech zurs", "lech zürs", "lech/zürs"],
     "display": "Lech/Zürs", "lat": 47.2112, "lon": 10.1439, "state": "AT", "country": "AT", "elev": 1444,
     "postal_code": "6763", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    # Italy
    {"names": ["cortina", "cortina d'ampezzo"],
     "display": "Cortina d'Ampezzo", "lat": 46.5381, "lon": 12.1372, "state": "IT", "country": "IT", "elev": 1224,
     "postal_code": "32043", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["madonna di campiglio", "campiglio"],
     "display": "Madonna di Campiglio", "lat": 46.2270, "lon": 10.8270, "state": "IT", "country": "IT", "elev": 1522,
     "postal_code": "38086", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    # Andorra
    {"names": ["grandvalira", "soldeu", "andorra"],
     "display": "Grandvalira (Andorra)", "lat": 42.5769, "lon": 1.6678, "state": "AD", "country": "AD", "elev": 1849,
     "postal_code": "AD500", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    # =========================================================================
    # New Zealand — Open-Meteo only
    # =========================================================================
    {"names": ["coronet peak", "coronet peak nz"],
     "display": "Coronet Peak", "lat": -44.8788, "lon": 168.7016, "state": "NZ", "country": "NZ", "elev": 1649,
     "postal_code": "9300", "sc_id": 4424660, "snotel": None, "snotel_dist_mi": None},
    {"names": ["the remarkables", "remarkables", "remarkables nz"],
     "display": "The Remarkables", "lat": -45.0261, "lon": 168.7939, "state": "NZ", "country": "NZ", "elev": 1943,
     "postal_code": "9300", "sc_id": 615013, "snotel": None, "snotel_dist_mi": None},
    {"names": ["mt hutt", "mount hutt"],
     "display": "Mt. Hutt", "lat": -43.4667, "lon": 171.5333, "state": "NZ", "country": "NZ", "elev": 1100,
     "postal_code": "7700", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["cardrona", "cardrona alpine resort"],
     "display": "Cardrona Alpine Resort", "lat": -44.8500, "lon": 168.9500, "state": "NZ", "country": "NZ", "elev": 1260,
     "postal_code": "9382", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["treble cone"],
     "display": "Treble Cone", "lat": -44.6311, "lon": 168.8736, "state": "NZ", "country": "NZ", "elev": 1248,
     "postal_code": "9305", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
    {"names": ["ohau", "ohau snow fields"],
     "display": "Ohau Snow Fields", "lat": -44.2204, "lon": 169.7785, "state": "NZ", "country": "NZ", "elev": 1425,
     "postal_code": "7999", "sc_id": None, "snotel": None, "snotel_dist_mi": None},
]


# ---------------------------------------------------------------------------
# State / region index — built once at import time
# ---------------------------------------------------------------------------

REGION_GROUPS: dict[str, list[str]] = {
    "US Rockies":       ["CO", "ID", "MT", "NM", "UT", "WY"],
    "US West Coast":    ["AK", "CA", "NV", "OR", "WA"],
    "US East":          ["ME", "MN", "NH", "VT"],
    "Canadian Rockies": ["AB", "BC", "QC"],
    "Japan":            ["HK", "NG", "NI"],
    "Europe":           ["FR", "CH", "AT", "IT", "AD"],
    "New Zealand":      ["NZ"],
}


def get_resorts_by_region() -> dict[str, list[dict]]:
    """Returns {region_name: [resort_entries]} — resorts sorted alpha within each region."""
    from collections import defaultdict
    by_state: dict[str, list] = defaultdict(list)
    for entry in _RESORTS:
        by_state[entry["state"]].append(entry)
    result = {}
    for region, states in REGION_GROUPS.items():
        resorts = []
        for state in states:
            resorts.extend(by_state.get(state, []))
        if resorts:
            result[region] = sorted(resorts, key=lambda e: e["display"])
    return result


_RESORTS_BY_REGION: dict[str, list[dict]] = get_resorts_by_region()

# Build a fast lookup map from canonical name strings
_LOOKUP: dict[str, dict] = {}
for _entry in _RESORTS:
    for _alias in _entry["names"]:
        _LOOKUP[_alias] = _entry
_KEYS_LONGEST_FIRST: list[str] = sorted(_LOOKUP, key=len, reverse=True)


class SnowResolver:
    """Resolves free-text location input to a ResortLocation."""

    def resolve(self, raw: str) -> ResortLocation:
        """
        Resolve a raw location string to a ResortLocation.
        Raises ValueError if resolution fails completely.
        """
        loc = self.match_known(raw)
        if loc:
            return loc

        # 3. Nominatim geocoding fallback
        loc = self._geocode_fallback(raw)
        if loc:
            return loc

        raise ValueError(f"Could not resolve location: {raw!r}")

    def match_known(self, raw: str) -> Optional[ResortLocation]:
        """Match against the resort table only (offline); None if no resort is named."""
        normalised = raw.strip().lower()

        # 1. Exact match
        entry = _LOOKUP.get(normalised)
        if entry:
            return _entry_to_location(entry)

        # 2. Whole-word match, longest alias first: an alias inside the input
        #    ("what's the snow like at vail?"), or the input as the start of an
        #    alias ("mammoth" → "mammoth mountain"). Word boundaries stop short
        #    inputs like "ok" matching inside "revelstoke".
        for key in _KEYS_LONGEST_FIRST:
            e = _LOOKUP[key]
            if (re.search(rf"\b{re.escape(key)}\b", normalised)
                    or (len(normalised) >= 4 and re.match(rf"{re.escape(normalised)}\b", key))):
                logger.debug("[resolver] Fuzzy match %r → %r", normalised, e["display"])
                return _entry_to_location(e)
        return None

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
        country=entry.get("country", "US"),
        elevation_m=entry.get("elev"),
        postal_code=entry.get("postal_code"),
        snocountry_id=entry.get("sc_id"),
        snotel_triplet=entry.get("snotel"),
        snotel_dist_mi=entry.get("snotel_dist_mi"),
    )
