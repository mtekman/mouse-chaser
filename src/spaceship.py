from dataclasses import dataclass

@dataclass
class SpacecraftState:
    scale: float = 1.0
    angle_deg: float = 0.0
    stretch: float = 1.0
    speed: float = 0.0
    acceleration: float = 0.0
    distance_opacity: float = 1.0

def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def lerp(current: float, target: float, amount: float) -> float:
    return current + (target - current) * amount


def lerp_angle(current: float, target: float, amount: float) -> float:
    """Interpolate through the shortest circular route."""
    delta = (target - current + 180.0) % 360.0 - 180.0
    return (current + delta * amount) % 360.0
