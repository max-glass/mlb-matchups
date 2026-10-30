"""Park factors, roof status, and air density.

Weather adjustments are deviations from a standard-atmosphere reference.
They are shrunk toward 1 because the park factor already contains a typical climate.
A closed roof does not receive an outdoor air-density adjustment.
"""

from __future__ import annotations

import math


def standard_density(elevation_m: float) -> float:
    temperature = 288.15 - 0.0065 * max(elevation_m, 0.0)
    pressure = 101325.0 * (temperature / 288.15) ** 5.255
    return pressure / (287.05 * temperature)


def air_density(temp_c: float, relative_humidity: float, pressure_msl_hpa: float, elevation_m: float) -> float:
    temp_k = temp_c + 273.15
    station_hpa = pressure_msl_hpa * math.exp(-elevation_m / (29.3 * temp_k))
    saturation = 6.1078 * 10 ** (7.5 * temp_c / (temp_c + 237.3))
    vapor = (relative_humidity / 100.0) * saturation
    dry = station_hpa - vapor
    return (dry * 100.0) / (287.058 * temp_k) + (vapor * 100.0) / (461.495 * temp_k)


def shrunk_park_multiplier(factor_100: float | None) -> float:
    if factor_100 is None:
        return 1.0
    return 1.0 + 0.5 * ((factor_100 / 100.0) - 1.0)


def weather_hr_multiplier(density: float | None, reference: float | None, roof_closed: bool) -> float:
    if roof_closed or density is None or reference is None or reference <= 0 or density <= 0:
        return 1.0
    raw = (reference / density) ** 0.35
    raw = min(1.08, max(0.92, raw))
    return 1.0 + 0.5 * (raw - 1.0)


def roof_is_closed(roof_type: str, condition: str | None) -> bool:
    if roof_type == "fixed_dome":
        return True
    text = (condition or "").lower()
    if roof_type == "retractable" and any(word in text for word in ("dome", "roof", "closed")):
        return True
    return False
