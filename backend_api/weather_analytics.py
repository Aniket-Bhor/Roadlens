"""
RoadLens Backend - Weather-Aware Road Quality & Predictive Analytics Engine
Predicts road degradation, hydroplaning risk, and dynamic maintenance urgency
across changing meteorological conditions (Clear, Rain/Monsoon, Extreme Heat, Fog).
"""

from typing import Dict, Any, List
from datetime import datetime, timezone


def calculate_predictive_road_quality(
    potholes_count: int,
    avg_severity: float,
    weather_condition: str = "clear",
    vehicle_speed_kmh: float = 40.0,
) -> Dict[str, Any]:
    """
    Computes Road Quality Index (RQI 0-100), surface friction, hydroplaning probability,
    and weather-adjusted hazard multiplier.
    """
    condition = weather_condition.lower().strip()

    # Weather multipliers and environmental parameters
    if condition in ("rain", "monsoon", "heavy_rain", "storm"):
        weather_label = "Monsoon / Heavy Rain"
        weather_icon = "🌧️"
        friction_base = 0.38
        hydroplane_risk = min(95.0, 35.0 + (potholes_count * 12.0) + (vehicle_speed_kmh * 0.4))
        pothole_danger_factor = 3.2  # Potholes fill with water, becoming invisible tire-damaging traps
        recommended_speed = max(25, int(60 * 0.55))
        rutting_risk = 45.0
        visibility_pct = 60.0
        degradation_rate = "Accelerated (3.2x normal water intrusion)"
    elif condition in ("heat", "extreme_heat", "summer"):
        weather_label = "Extreme Heat (>42°C)"
        weather_icon = "☀️"
        friction_base = 0.72
        hydroplane_risk = 5.0
        pothole_danger_factor = 1.6  # Bitumen softening and edge crumbling
        recommended_speed = 50
        rutting_risk = 82.0
        visibility_pct = 100.0
        degradation_rate = "Moderate (Bitumen softening & thermal rutting)"
    elif condition in ("fog", "mist", "low_visibility"):
        weather_label = "Dense Fog / Low Visibility"
        weather_icon = "🌫️"
        friction_base = 0.65
        hydroplane_risk = 20.0
        pothole_danger_factor = 2.4  # Delayed driver reaction time
        recommended_speed = 35
        rutting_risk = 15.0
        visibility_pct = 30.0
        degradation_rate = "Normal"
    else:
        # Default Dry / Clear
        weather_label = "Dry & Clear"
        weather_icon = "☀️"
        friction_base = 0.88
        hydroplane_risk = 4.0
        pothole_danger_factor = 1.0
        recommended_speed = 60
        rutting_risk = 20.0
        visibility_pct = 100.0
        degradation_rate = "Baseline"

    # Base Road Quality Index (100 = Brand new highway, 0 = Impassable)
    # Deductions based on pothole density and severity
    damage_penalty = (potholes_count * 8.5) + (avg_severity * 6.0)
    weather_penalty = (1.0 - friction_base) * 35.0

    raw_rqi = 100.0 - damage_penalty - weather_penalty
    rqi_score = max(10, min(98, round(raw_rqi, 1)))

    # Classification
    if rqi_score >= 80:
        quality_rating = "EXCELLENT"
        color_code = "#00F5D4"  # Emerald Cyan
        urgency = "ROUTINE_MONITORING"
    elif rqi_score >= 60:
        quality_rating = "SATISFACTORY"
        color_code = "#70E000"  # Green
        urgency = "SCHEDULED_REPAIRS"
    elif rqi_score >= 40:
        quality_rating = "DEGRADED"
        color_code = "#FFB703"  # Amber
        urgency = "HIGH_PRIORITY_DISPATCH"
    else:
        quality_rating = "CRITICAL_HAZARD"
        color_code = "#E63946"  # Crimson Red
        urgency = "EMERGENCY_MOBILIZATION"

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "weather_condition": condition,
        "weather_label": weather_label,
        "weather_icon": weather_icon,
        "road_quality_index": rqi_score,
        "quality_rating": quality_rating,
        "color_code": color_code,
        "surface_friction_coefficient": round(friction_base, 2),
        "hydroplaning_risk_pct": round(hydroplane_risk, 1),
        "pothole_danger_multiplier": round(pothole_danger_factor, 1),
        "recommended_speed_kmh": recommended_speed,
        "bitumen_rutting_risk_pct": round(rutting_risk, 1),
        "driver_visibility_pct": round(visibility_pct, 1),
        "degradation_rate": degradation_rate,
        "maintenance_urgency": urgency,
        "n8n_dispatch_recommended": rqi_score < 55 or pothole_danger_factor >= 2.0,
    }
