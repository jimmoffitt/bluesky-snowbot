"""
SnowFormatter — formats a SnowReport into a list of Bluesky post strings.

Each string is ≤ 300 characters (Bluesky post limit).

Output format depends on data availability:

  With SnoCountry (resort-reported data):
    Post 1: Resort + status + base depth + new snow (resort numbers)
    Post 2: Lifts/trails open + temp/wind (Open-Meteo) + surface condition

  Without SnoCountry (Open-Meteo only):
    Post 1: Resort + snow depth + 24/48/72h accumulation (estimated)
    Post 2: Temperature + wind + sky conditions

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
        if report.operations:
            return [
                self._post1_snocountry(report),
                self._post2_snocountry(report),
            ]
        return [
            self._post1_openmeteo(report),
            self._post2_conditions(report),
        ]

    # ------------------------------------------------------------------
    # SnoCountry-powered posts
    # ------------------------------------------------------------------

    def _post1_snocountry(self, report: SnowReport) -> str:
        """Resort status + base depth + new snow from resort-reported data."""
        loc  = report.location
        ops  = report.operations

        elev_str = _elev_str(loc.elevation_m)
        status   = ops.operating_status or "Status unknown"

        # Base depth — prefer SnoCountry; fall back to Open-Meteo
        if ops.base_depth_min_cm is not None and ops.base_depth_max_cm is not None:
            base_in_lo = _cm_to_in(ops.base_depth_min_cm)
            base_in_hi = _cm_to_in(ops.base_depth_max_cm)
            base_cm_lo = int(ops.base_depth_min_cm)
            base_cm_hi = int(ops.base_depth_max_cm)
            base_str = f'{base_in_lo:.0f}–{base_in_hi:.0f}" / {base_cm_lo}–{base_cm_hi} cm'
        elif ops.base_depth_min_cm is not None:
            base_str = f'{_cm_to_in(ops.base_depth_min_cm):.0f}" / {int(ops.base_depth_min_cm)} cm'
        else:
            # Open-Meteo fallback
            d = report.snow.depth_cm
            base_str = f'{_cm_to_in(d):.0f}" / {d:.0f} cm (est.)'

        # New snow 24h
        if ops.new_snow_24h_min_cm is not None and ops.new_snow_24h_max_cm is not None:
            lo = _cm_to_in(ops.new_snow_24h_min_cm)
            hi = _cm_to_in(ops.new_snow_24h_max_cm)
            new24_str = f'{lo:.0f}–{hi:.0f}"'
        elif ops.new_snow_24h_min_cm is not None:
            new24_str = f'{_cm_to_in(ops.new_snow_24h_min_cm):.1f}"'
        else:
            new24_str = f'{_cm_to_in(report.snow.new_24h_cm):.1f}" (est.)'

        # 48h snowfall
        if ops.snow_48h_cm is not None:
            new48_str = f'{_cm_to_in(ops.snow_48h_cm):.1f}"'
        else:
            new48_str = f'{_cm_to_in(report.snow.new_48h_cm):.1f}" (est.)'

        # Season total
        season_str = ""
        if ops.season_total_cm:
            season_str = f'\n   Season total: {_cm_to_in(ops.season_total_cm):.0f}"'

        text = (
            f"⛷️ {loc.name}, {loc.state}{elev_str}\n"
            f"Status: {status}\n\n"
            f"❄️ Base depth:  {base_str}\n"
            f"   New (24h):  {new24_str}\n"
            f"   New (48h):  {new48_str}"
            f"{season_str}"
        )
        return _truncate(text)

    def _post2_snocountry(self, report: SnowReport) -> str:
        """Lifts/trails + temp/wind + surface condition."""
        ops  = report.operations
        cond = report.conditions

        temp_f  = _c_to_f(cond.temp_c)
        wind_mp = _kmh_to_mph(cond.wind_speed_kmh)
        gust_mp = _kmh_to_mph(cond.wind_gusts_kmh)

        # Lifts line
        lift_str = ""
        if ops.open_lifts is not None and ops.total_lifts is not None:
            lift_str = f"🚡 Lifts:    {ops.open_lifts}/{ops.total_lifts} open"
        elif ops.open_lifts is not None:
            lift_str = f"🚡 Lifts:    {ops.open_lifts} open"

        # Trails line
        trail_str = ""
        if ops.open_trails is not None and ops.total_trails is not None:
            pct = f" ({ops.open_percent:.0f}%)" if ops.open_percent else ""
            trail_str = f"\n🎿 Trails:   {ops.open_trails}/{ops.total_trails} open{pct}"
        elif ops.open_trails is not None:
            trail_str = f"\n🎿 Trails:   {ops.open_trails} open"

        # Surface condition
        surface_str = ""
        if ops.surface_condition:
            surface_str = f"\n❄️ Surface:  {ops.surface_condition}"

        # Temperature and wind
        weather_line = (
            f"\n🌡️ Temp:     {temp_f:.0f}°F / {cond.temp_c:.0f}°C"
            f"\n💨 Wind:     {wind_mp:.0f} mph / {cond.wind_speed_kmh:.0f} km/h  "
            f"gusts {gust_mp:.0f} mph"
        )

        text = lift_str + trail_str + surface_str + weather_line
        return _truncate(text.lstrip("\n"))

    # ------------------------------------------------------------------
    # Open-Meteo only posts (fallback)
    # ------------------------------------------------------------------

    def _post1_openmeteo(self, report: SnowReport) -> str:
        """Snow depth and recent snowfall totals (estimated from Open-Meteo)."""
        loc  = report.location
        snow = report.snow

        depth_in = _cm_to_in(snow.depth_cm)
        elev_str = _elev_str(loc.elevation_m)

        text = (
            f"⛷️ {loc.name}, {loc.state}{elev_str}\n\n"
            f"❄️ Base depth: {depth_in:.0f}\" / {snow.depth_cm:.0f} cm (est.)\n"
            f"   New (24h):  {_cm_to_in(snow.new_24h_cm):.1f}\" / {snow.new_24h_cm:.1f} cm\n"
            f"   New (48h):  {_cm_to_in(snow.new_48h_cm):.1f}\" / {snow.new_48h_cm:.1f} cm\n"
            f"   New (72h):  {_cm_to_in(snow.new_72h_cm):.1f}\" / {snow.new_72h_cm:.1f} cm"
        )
        return _truncate(text)

    def _post2_conditions(self, report: SnowReport) -> str:
        """Temperature, wind, and sky conditions from Open-Meteo."""
        cond = report.conditions

        temp_f  = _c_to_f(cond.temp_c)
        wind_mp = _kmh_to_mph(cond.wind_speed_kmh)
        gust_mp = _kmh_to_mph(cond.wind_gusts_kmh)

        vis_str = ""
        if cond.visibility_km is not None:
            vis_str = f"\n🔭 Visibility:  {cond.visibility_km * 0.621:.1f} mi / {cond.visibility_km:.1f} km"

        text = (
            f"🌡️ Temp:     {temp_f:.0f}°F / {cond.temp_c:.0f}°C\n"
            f"💨 Wind:     {wind_mp:.0f} mph / {cond.wind_speed_kmh:.0f} km/h\n"
            f"   Gusts:   {gust_mp:.0f} mph / {cond.wind_gusts_kmh:.0f} km/h\n"
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

def _elev_str(elevation_m) -> str:
    if elevation_m is None:
        return ""
    elev_ft = int(elevation_m * 3.281)
    return f"  ({elev_ft:,} ft / {int(elevation_m):,} m)"

def _truncate(text: str, limit: int = BLUESKY_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit - 1] + "…"
