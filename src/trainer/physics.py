"""Virtual road physics: convert watts → flat-road speed.

The trainer's reported speed depends on its internal gear ratio (and with a
Zwift Cog there's only one ratio), so it tracks cadence, not effort. We want
"as if riding flat" speed: solve the standard cycling power equation
    P = ½·ρ·CdA·v³  +  Crr·m·g·v
for v, given the rider's instantaneous power.
"""
from __future__ import annotations


# Defaults chosen for "average road cyclist on the hoods, flat tarmac, no wind".
# These can be tuned later from a settings file.
DEFAULT_MASS_KG     = 82.0    # rider + bike
DEFAULT_CRR         = 0.005   # rolling resistance coefficient
DEFAULT_CDA         = 0.30    # drag-area product (m²)
DEFAULT_AIR_DENSITY = 1.225   # kg/m³ at sea level, 15 °C
DRIVETRAIN_LOSS     = 0.02    # 2% — power that doesn't reach the road
GRAVITY             = 9.81


def virtual_speed_kph(
    power_w: float | int | None,
    *,
    mass_kg: float = DEFAULT_MASS_KG,
    crr: float = DEFAULT_CRR,
    cda: float = DEFAULT_CDA,
    air_density: float = DEFAULT_AIR_DENSITY,
    drivetrain_loss: float = DRIVETRAIN_LOSS,
) -> float:
    """Solve a·v³ + b·v − P_eff = 0 for forward speed; return km/h.

    Uses Newton's method (converges in <10 iterations for any sane power).
    """
    if power_w is None or power_w <= 0:
        return 0.0
    p_eff = float(power_w) * (1.0 - drivetrain_loss)
    a = 0.5 * air_density * cda
    b = crr * mass_kg * GRAVITY

    # Initial guess: aero-only solution. Bounded so we never start at 0.
    v = max((p_eff / max(a, 1e-9)) ** (1.0 / 3.0), 0.5)
    for _ in range(15):
        f = a * v * v * v + b * v - p_eff
        fp = 3.0 * a * v * v + b
        if fp <= 0:
            break
        delta = f / fp
        v -= delta
        if abs(delta) < 1e-4:
            break
    return max(0.0, v) * 3.6
