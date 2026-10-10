"""
RoadLens Backend - Pydantic Data Contract & Schemas
"""

from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field, field_validator


class TelemetryPayload(BaseModel):
    """
    Incoming mobile edge telemetry payload contract from RoadLens Scout client.
    Contract: { device_id, timestamp, hazard_class, confidence, latitude, longitude, heading, bbox }
    """
    device_id: str = Field(..., description="Unique client hardware/session identifier", example="scout-edge-7f8a")
    timestamp: str = Field(..., description="ISO-8601 string or epoch string timestamp", example="2026-10-10T02:30:00Z")
    hazard_class: str = Field(..., description="Classified hazard type (e.g. pothole, debris, flood, crack, barrier)", example="pothole")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Detection confidence score between 0.0 and 1.0", example=0.88)
    latitude: Optional[float] = Field(default=None, ge=-90.0, le=90.0, description="WGS84 Latitude", example=19.0760)
    longitude: Optional[float] = Field(default=None, ge=-180.0, le=180.0, description="WGS84 Longitude", example=72.8777)
    heading: Optional[float] = Field(default=None, ge=0.0, le=360.0, description="Vehicle compass heading in degrees (0 - 360)", example=135.0)
    bbox: List[float] = Field(..., min_length=4, max_length=4, description="Bounding box [x, y, width, height]", example=[96.0, 140.0, 128.0, 64.0])

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamp(cls, v) -> str:
        """
        Accepts ISO strings, epoch integers, or epoch floats and normalizes to standard ISO string.
        """
        if isinstance(v, (int, float)):
            # If milliseconds timestamp
            if v > 1e11:
                v = v / 1000.0
            from datetime import timezone
            return datetime.fromtimestamp(v, tz=timezone.utc).isoformat()
        return str(v)

    @field_validator("bbox", mode="before")
    @classmethod
    def normalize_bbox(cls, v) -> List[float]:
        """
        Accepts either an array [x, y, width, height] or an object {x, y, width, height}.
        """
        if isinstance(v, dict):
            return [
                float(v.get("x", 0.0)),
                float(v.get("y", 0.0)),
                float(v.get("width", 0.0)),
                float(v.get("height", 0.0)),
            ]
        if isinstance(v, (list, tuple)):
            if len(v) != 4:
                raise ValueError("bbox must contain exactly 4 coordinates [x, y, width, height]")
            return [float(x) for x in v]
        raise ValueError("bbox must be a list of 4 numbers or an object {x, y, width, height}")


class TelemetryIngestResponse(BaseModel):
    """
    Response returned to edge client upon successful receipt & queueing.
    """
    status: str = "received"
    hazard_id: Optional[str] = None
    device_id: str
    hazard_class: str
    timestamp: str
    message: str = "Telemetry payload successfully queued for PostGIS ingestion and clustering"


class HealthResponse(BaseModel):
    """
    System uptime & connectivity status response.
    """
    status: str
    version: str
    timestamp: str
    database_connected: bool
    db_mode: str


class HazardClusterRecord(BaseModel):
    """
    Representing a clustered and verified hazard record.
    """
    cluster_id: str
    hazard_class: str
    center_latitude: float
    center_longitude: float
    member_count: int
    aggregate_severity: float
    status: str = "VERIFIED"
    first_detected_at: str
    last_detected_at: str
