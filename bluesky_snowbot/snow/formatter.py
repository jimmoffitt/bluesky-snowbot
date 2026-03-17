"""
SnowFormatter — formats a SnowReport into a list of Bluesky post strings.

Each string is ≤ 300 characters (Bluesky post limit).
The default output is a 2-post thread:
  Post 1: Resort name, snow depth, recent snowfall totals
  Post 2: Temperature, wind, current conditions description

Usage:
    from bluesky_snowbot.snow.formatter import SnowFormatter
    posts = SnowFormatter().format_thread(report)
"""

from __future__ import annotations

from bluesky_snowbot.snow.models import SnowReport

BLUESKY_LIMIT = 300


class SnowFormatter:

    def format_thread(self, report: SnowReport) -> list[str]:
        """Return a list of ≤300-char strings forming a post thread."""
        return [
            self._post1_snow(report),
            self._post2_conditions(report),
        ]

    # ------------------------------------------------------------------
    # Post builders
    # ------------------------------------------------------------------

    def _post1_snow(self, report: SnowReport) -> str:
        """Snow depth and recent snowfall totals (dual units)."""
        loc   = report.location
        snow  = report.snow

        depth_cm  = snow.depth_cm
        depth_in  = _cm_to_in(depth_cm)

        new24_cm  = snow.new_24h_cm
        new24_in  = _cm_to_in(new24_cm)
        new48_cm  = snow.new_48h_cm
        new48_in  = _cm_to_in(new48_cm)
        new72_cm  = snow.new_72h_cm
        new72_in  = _cm_to_in(new72_cm)

        elev_str = ""
        if loc.elevation_m:
            elev_ft = int(loc.elevation_m * 3.281)
            elev_str = f"  ({elev_ft:,} ft / {int(loc.elevation_m):,} m)"

        text = (
            f"⛷️ {loc.name}, {loc.state}{elev_str}\n\n"
            f"❄️ Base depth: {depth_in:.0f}\" / {depth_cm:.0f} cm\n"
            f"   New (24h):  {new24_in:.1f}\" / {new24_cm:.1f} cm\n"
            f"   New (48h):  {new48_in:.1f}\" / {new48_cm:.1f} cm\n"
            f"   New (72h):  {new72_in:.1f}\" / {new72_cm:.1f} cm"
        )
        return _truncate(text)

    def _post2_conditions(self, report: SnowReport) -> str:
        """Temperature, wind, and sky conditions."""
        cond = report.conditions

        temp_c  = cond.temp_c
        temp_f  = _c_to_f(temp_c)
        wind_kh = cond.wind_speed_kmh
        wind_mp = _kmh_to_mph(wind_kh)
        gust_kh = cond.wind_gusts_kmh
        gust_mp = _kmh_to_mph(gust_kh)

        vis_str = ""
        if cond.visibility_km is not None:
            vis_mi = cond.visibility_km * 0.621
            vis_str = f"\n🔭 Visibility:  {vis_mi:.1f} mi / {cond.visibility_km:.1f} km"

        text = (
            f"🌡️ Temp:     {temp_f:.0f}°F / {temp_c:.0f}°C\n"
            f"💨 Wind:     {wind_mp:.0f} mph / {wind_kh:.0f} km/h\n"
            f"   Gusts:   {gust_mp:.0f} mph / {gust_kh:.0f} km/h\n"
            f"🌤️ Skies:    {cond.weather_desc}"
            f"{vis_str}"
        )
        return _truncate(text)


# ---------------------------------------------------------------------------
# Unit conversion helpers
# ---------------------------------------------------------------------------

def _cm_to_in(cm: float) -> float:
    return cm / 2.54

def _c_to_f(c: float) -> float:
    return c * 9 / 5 + 32

def _kmh_to_mph(kmh: float) -> float:
    return kmh * 0.621

def _truncate(text: str, limit: int = BLUESKY_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit - 1] + "…"
