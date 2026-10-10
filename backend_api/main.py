"""
RoadLens Backend - FastAPI Ingestion & Spatial Analytics API
"""

import os
import logging
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException, status, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import shutil
import uuid
from video_processor import process_video_file

from models import TelemetryPayload, TelemetryIngestResponse, HealthResponse
from db import (
    insert_hazard,
    check_database_connection,
    get_supabase_client,
    list_recent_hazards,
    list_recent_clusters,
)
from clustering import run_spatiotemporal_clustering
from traffic_incidents import (
    search_road_traffic,
    get_popular_roads,
    dispatch_n8n_incident_alert,
    get_all_active_incidents,
)

load_dotenv()

# Configure structured logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("roadlens.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application startup and shutdown lifespan handler.
    """
    logger.info("Initializing RoadLens Backend API...")
    # Initialize Supabase client on startup
    get_supabase_client()
    yield
    logger.info("RoadLens Backend API shutting down...")


app = FastAPI(
    title="RoadLens Scout Ingestion & Spatial Analytics API",
    description="High-throughput edge client telemetry ingestion, PostGIS integration, and DBSCAN spatiotemporal clustering.",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for edge clients and downstream tools
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse, tags=["Monitoring"])
async def health_check() -> HealthResponse:
    """
    System uptime & database connectivity health check.
    """
    is_connected, mode = check_database_connection()
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        timestamp=datetime.now(timezone.utc).isoformat(),
        database_connected=is_connected,
        db_mode=mode,
    )


@app.post(
    "/api/v1/telemetry",
    response_model=TelemetryIngestResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Ingestion"],
)
async def ingest_telemetry(
    payload: TelemetryPayload,
    background_tasks: BackgroundTasks,
) -> TelemetryIngestResponse:
    """
    Receives validated mobile edge hazard detection telemetry from the Scout client.
    
    1. Validates the payload contract:
       { device_id, timestamp, hazard_class, confidence, latitude, longitude, heading, bbox }
    2. Persists the record into PostGIS 'hazards' table.
    3. Asynchronously triggers the DBSCAN spatiotemporal clustering engine.
    """
    try:
        # Ingest into PostGIS / Supabase
        record = await insert_hazard(payload)
        hazard_id = record.get("id")

        # Asynchronously schedule the DBSCAN clustering engine to prevent duplicate records
        background_tasks.add_task(run_spatiotemporal_clustering)

        return TelemetryIngestResponse(
            status="received",
            hazard_id=hazard_id,
            device_id=payload.device_id,
            hazard_class=payload.hazard_class,
            timestamp=payload.timestamp,
            message="Hazard telemetry successfully ingested and queued for DBSCAN deduplication.",
        )
    except Exception as e:
        logger.error(f"[API] Error ingesting telemetry from device {payload.device_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Telemetry ingestion failed: {str(e)}",
        )


@app.post(
    "/api/v1/clustering/run",
    tags=["Analytics"],
)
async def trigger_clustering_manually() -> Dict[str, Any]:
    """
    Manually triggers the DBSCAN spatiotemporal clustering engine.
    Useful for testing or scheduled cron job triggers.
    """
    clusters = await run_spatiotemporal_clustering()
    return {
        "status": "completed",
        "clusters_formed": len(clusters),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "clusters": clusters,
    }


@app.get(
    "/api/v1/hazards",
    tags=["Inspection"],
)
async def get_recent_hazards(limit: int = 50) -> Dict[str, Any]:
    """
    Returns recent raw ingested road hazard telemetry reports.
    """
    hazards = await list_recent_hazards(limit=limit)
    return {
        "count": len(hazards),
        "hazards": hazards,
    }


@app.get(
    "/api/v1/clusters",
    tags=["Inspection"],
)
async def get_recent_clusters(limit: int = 50) -> Dict[str, Any]:
    """
    Returns recent merged and verified ground-truth hazard clusters.
    """
    clusters = await list_recent_clusters(limit=limit)
    return {
        "count": len(clusters),
        "clusters": clusters,
    }


@app.get(
    "/api/v1/analytics/road-quality",
    tags=["Weather & Predictive Analytics"],
)
async def get_road_quality_prediction(
    weather: str = "clear",
    speed_kmh: float = 40.0,
) -> Dict[str, Any]:
    """
    Computes real-time and predictive Road Quality Index (RQI) across changing
    meteorological conditions (Clear, Rain/Monsoon, Heat, Fog).
    """
    from weather_analytics import calculate_predictive_road_quality
    hazards = await list_recent_hazards(limit=30)
    avg_sev = 1.0
    if hazards:
        severities = [float(h.get("severity_score", 1.0) or 1.0) for h in hazards]
        avg_sev = sum(severities) / len(severities)

    prediction = calculate_predictive_road_quality(
        potholes_count=len(hazards),
        avg_severity=avg_sev,
        weather_condition=weather,
        vehicle_speed_kmh=speed_kmh,
    )
    prediction["active_hazards_evaluated"] = len(hazards)
    return prediction


@app.get(
    "/api/v1/traffic/search",
    tags=["Traffic & Incident Intelligence"],
)
async def search_traffic_by_road(
    query: str,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    incident_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Searches for a road/location to assess real-time traffic congestion, average speed,
    blockage status, and active incidents (tempo accidents or natural disasters).
    """
    if not query or not query.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Road name or location query cannot be empty.",
        )
    return await search_road_traffic(
        query=query,
        user_lat=latitude,
        user_lon=longitude,
        incident_type_override=incident_type,
    )


@app.get(
    "/api/v1/traffic/popular-roads",
    tags=["Traffic & Incident Intelligence"],
)
async def list_popular_traffic_corridors() -> Dict[str, Any]:
    """
    Returns quick-selection list of popular roads with live incident and blockage summaries.
    """
    roads = get_popular_roads()
    return {"count": len(roads), "roads": roads}


@app.post(
    "/api/v1/traffic/dispatch-incident-alert",
    tags=["Traffic & Incident Intelligence"],
)
async def trigger_n8n_incident_dispatch(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Triggers the n8n emergency incident workflow and broadcasts the emergency alert.
    """
    return await dispatch_n8n_incident_alert(payload)


@app.get(
    "/api/v1/traffic/active-incidents",
    tags=["Traffic & Incident Intelligence"],
)
async def get_active_road_incidents() -> Dict[str, Any]:
    """
    Returns all active road blockages, tempo accidents, and natural disasters
    for real-time plotting on the interactive Radar Map.
    """
    incidents = get_all_active_incidents()
    return {"count": len(incidents), "incidents": incidents}


@app.post(
    "/api/v1/videos/process",
    tags=["Video AI Analytics"],
)
async def upload_and_process_video(
    video: UploadFile = File(..., description="Roadway dashcam MP4/MOV video file"),
    device_id: Optional[str] = Form("scout-mobile-01"),
    latitude: Optional[str] = Form("18.464928"),
    longitude: Optional[str] = Form("73.836153"),
    heading: Optional[str] = Form("348.0"),
    speed_kmh: Optional[str] = Form("30.0"),
    confidence_threshold: Optional[str] = Form("0.25"),
) -> Dict[str, Any]:
    """
    Receives an uploaded mobile dashcam video, executes YOLOv8 pothole detection
    using the trained weights from pothole_v1, extracts geographic detections,
    and runs DBSCAN spatiotemporal clustering.
    """
    upload_dir = os.path.join(os.path.dirname(__file__), "uploads")
    os.makedirs(upload_dir, exist_ok=True)

    # Validate and safely convert form parameters
    try:
        lat_val = float(latitude) if latitude else 18.464928
    except (ValueError, TypeError):
        lat_val = 18.464928

    try:
        lon_val = float(longitude) if longitude else 73.836153
    except (ValueError, TypeError):
        lon_val = 73.836153

    try:
        heading_val = float(heading) if heading else 348.0
    except (ValueError, TypeError):
        heading_val = 348.0

    try:
        speed_val = float(speed_kmh) if speed_kmh else 30.0
    except (ValueError, TypeError):
        speed_val = 30.0

    try:
        conf_val = float(confidence_threshold) if confidence_threshold else 0.25
    except (ValueError, TypeError):
        conf_val = 0.25

    dev_id = device_id or "scout-mobile-01"

    # Validate video stream not empty
    try:
        video.file.seek(0, os.SEEK_END)
        file_size = video.file.tell()
        video.file.seek(0)
    except Exception:
        file_size = 1

    if file_size == 0:
        logger.error(f"[API] Empty video upload received: {video.filename}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded video file is empty (0 bytes). Please pick a valid road video.",
        )

    raw_ext = os.path.splitext(video.filename or "video.mp4")[1].lower()
    file_ext = raw_ext if raw_ext in [".mp4", ".mov", ".avi", ".mkv", ".webm", ".3gp"] else ".mp4"
    unique_filename = f"video_{uuid.uuid4().hex[:8]}{file_ext}"
    temp_file_path = os.path.join(upload_dir, unique_filename)

    try:
        # Save uploaded video file to disk
        with open(temp_file_path, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)

        logger.info(f"[API] Processing uploaded video {video.filename} ({file_size} bytes -> {temp_file_path}) with conf={conf_val}...")

        # Process with trained YOLOv8 model
        result = await process_video_file(
            video_path=temp_file_path,
            device_id=dev_id,
            start_latitude=lat_val,
            start_longitude=lon_val,
            heading=heading_val,
            vehicle_speed_kmh=speed_val,
            confidence_threshold=conf_val,
        )

        return result
    except Exception as e:
        logger.error(f"[API] Error processing video {video.filename}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Video AI processing failed: {str(e)}",
        )
    finally:
        # Clean up temporary uploaded file
        if os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception:
                pass


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host=host, port=port, reload=True)
