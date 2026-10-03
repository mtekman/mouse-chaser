from pathlib import Path
from yaml import safe_load

def load_config(path: Path) -> tuple[dict, bytes]:
    """Load TOML settings and the configured SVG file."""
    with path.open("rb") as config_file:
        config = safe_load(config_file)

    craft = config["craft"]["spaceship"]
    profile = config["physics_profile"]["profile1"]
    application = config["application"]
    
    overlay_size = int(float(profile["visual"]["size_base"])
                    * float(profile["visual"]["scale_max"])
                    * max(float(profile["visual"]["elong_max"]), 1.0)
                    * 6)

    conf = {
        "BEZIER_LENGTH_SAMPLES": int(application["bezier"]["length_samples"]),
        "PHYS_DEAD_ZONE": float(application["dead_zone"]),
        "STATIONARY_TICKS_REQUIRED": int(application["stationary"]["ticks_required"]),
        "STATIONARY_RADIUS": float(application["stationary"]["radius_beg"]),
        "STATIONARY_RESET_RADIUS": float(application["stationary"]["radius_end"]),
        "DEBUG_DRAW_BEZIER": bool(application["bezier"]["debug_draw"]),
        "BEZIER_REPLAN_DISTANCE": float(application["replan_distance"]),
        "POLL_MS": int(application["poll_ms"]),
        "MOUSE_DOWN_RECALC_INTERVAL": int(application["mouse_down_recalc_interval"]),
        ##
        "VIZ_SIZE_BASE": float(profile["visual"]["size_base"]),
        "VIZ_ANGLE_OFFSET": float(profile["visual"]["angle_offset"]),
        "VIZ_SPEED_MULT": float(profile["visual"]["speed_mult"]),
        "VIZ_ACCEL_MULT": float(profile["visual"]["accel_mult"]),
        "VIZ_SCALE_MAX": float(profile["visual"]["scale_max"]),
        "VIZ_ELONG_MAX": float(profile["visual"]["elong_max"]),
        "VIZ_SHAPE_SMOOTH": float(profile["visual"]["shape_smooth"]),
        "LIN_SPEED_MAX": float(profile["linear_motion"]["speed_max"]),
        "LIN_SPEED_MIN": float(profile["linear_motion"]["speed_min"]),
        "LIN_SPEED_DRAG": float(profile["linear_motion"]["speed_drag"]),
        ##
        "BEZIER_WAYPOINT_CURVE_FRACTION": float(profile["bezier"]["waypoint_curve_fraction"]),
        "BEZIER_START_HANDLE_FRACTION": float(profile["bezier"]["start_handle_fraction"]),
        "BEZIER_START_HANDLE_MAX": float(profile["bezier"]["start_handle_max"]),
        "BEZIER_END_HANDLE_FRACTION": float(profile["bezier"]["end_handle_fraction"]),
        "BEZIER_END_HANDLE_MAX": float(profile["bezier"]["end_handle_max"]),
        "BEZIER_MIN_SPEED": float(profile["bezier"]["min_speed"]),
        "BEZIER_ACCELERATION": float(profile["bezier"]["acceleration"]),
        ##
        "SPACESHIP_FADE_START_DISTANCE": float(craft["fade"]["distance_beg"]),
        "SPACESHIP_FADE_END_DISTANCE": float(craft["fade"]["distance_end"]),
        "SPACESHIP_MIN_OPACITY": float(craft["fade"]["opacity_min"]),
        "OVERLAY_SIZE": overlay_size,
        "GREEN_DOT_SIZE": int(craft["svg"]["cursor"] or 0)
    }
    svg_path = Path("svgs", craft["svg"]["chaser"]+".svg")
    with svg_path.open("br") as svg_file:
        spaceship_svg = svg_file.read()

    return conf, spaceship_svg


