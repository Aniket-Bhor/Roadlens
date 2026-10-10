"""
RoadLens Backend - YOLOv8 Video Processing Engine
Integrates trained model from pothole_v1 to analyze recorded dashcam video feeds,
detect road surface hazards (potholes), extract spatiotemporal coordinates,
and trigger DBSCAN clustering.
"""

import os
import math
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
import cv2
from ultralytics import YOLO

from models import TelemetryPayload
from db import insert_hazard
from clustering import run_spatiotemporal_clustering

logger = logging.getLogger("roadlens.video_processor")

# Path to trained YOLOv8 weights from pothole_v1
WEIGHTS_PATH = os.path.join(os.path.dirname(__file__), "weights", "best.pt")

_model: Optional[YOLO] = None


def get_yolo_model() -> YOLO:
    """
    Initializes and returns the singleton instance of the trained YOLOv8 model.
    """
    global _model
    if _model is not None:
        return _model

    if not os.path.exists(WEIGHTS_PATH):
        raise FileNotFoundError(f"YOLOv8 weights not found at {WEIGHTS_PATH}")

    logger.info(f"[YOLO] Loading trained road hazard weights from {WEIGHTS_PATH}...")
    _model = YOLO(WEIGHTS_PATH)
    logger.info(f"[YOLO] Model loaded successfully with classes: {_model.names}")
    return _model


def project_coordinates(
    start_lat: float,
    start_lon: float,
    heading_deg: float,
    distance_meters: float,
) -> tuple[float, float]:
    """
    Calculates the destination latitude and longitude given a starting coordinate,
    bearing in degrees, and distance traveled along the heading vector (Great Circle).
    """
    earth_radius = 6371000.0  # meters
    d_r = distance_meters / earth_radius

    lat_rad = math.radians(start_lat)
    lon_rad = math.radians(start_lon)
    bearing_rad = math.radians(heading_deg)

    new_lat_rad = math.asin(
        math.sin(lat_rad) * math.cos(d_r)
        + math.cos(lat_rad) * math.sin(d_r) * math.cos(bearing_rad)
    )

    new_lon_rad = lon_rad + math.atan2(
        math.sin(bearing_rad) * math.sin(d_r) * math.cos(lat_rad),
        math.cos(d_r) - math.sin(lat_rad) * math.sin(new_lat_rad),
    )

    return (
        round(math.degrees(new_lat_rad), 6),
        round(math.degrees(new_lon_rad), 6),
    )


async def process_video_file(
    video_path: str,
    device_id: str = "scout-dashcam-01",
    start_latitude: float = 18.464928,
    start_longitude: float = 73.836153,
    heading: float = 348.0,
    vehicle_speed_kmh: float = 30.0,
    confidence_threshold: float = 0.25,
    target_sampling_fps: float = 5.0,
) -> Dict[str, Any]:
    """
    Analyzes an uploaded roadway video file using the trained pothole_v1 YOLOv8 model.

    1. Samples video frames at 5 FPS to match edge camera frequency.
    2. Runs YOLOv8 inference to detect potholes.
    3. Projects continuous vehicle GPS coordinates based on vehicle heading and elapsed time.
    4. Automatically ingests detected hazard telemetry into PostGIS.
    5. Executes DBSCAN clustering to group repeat detections into verified clusters.
    """
    model = get_yolo_model()

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found at {video_path}")

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Could not open video file: {video_path}. Verify codec is supported.")

    video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if video_fps <= 0 or math.isnan(video_fps):
        video_fps = 30.0
    total_frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    video_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    video_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration_seconds = total_frame_count / video_fps if (video_fps > 0 and total_frame_count > 0) else 0.0

    # Calculate frame stride to sample at target_sampling_fps (e.g. 30 fps / 5 fps = every 6 frames)
    frame_stride = max(1, round(video_fps / target_sampling_fps))
    # If video is long, dynamically adjust stride to cap evaluated frames at 120 for swift processing
    if total_frame_count > 0 and (total_frame_count / frame_stride) > 120:
        frame_stride = max(1, total_frame_count // 120)

    speed_mps = (vehicle_speed_kmh * 1000.0) / 3600.0  # m/s

    logger.info(
        f"[VideoProcessor] Processing video: {os.path.basename(video_path)} "
        f"({total_frame_count} frames, {video_fps:.1f} FPS, {duration_seconds:.1f}s, stride={frame_stride}, conf={confidence_threshold})"
    )

    detected_hazards: List[Dict[str, Any]] = []
    current_frame_idx = 0
    sampled_frames_count = 0
    start_time = datetime.now(timezone.utc)

    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                break

            if current_frame_idx % frame_stride == 0:
                sampled_frames_count += 1
                elapsed_sec = current_frame_idx / video_fps
                distance_traveled_m = elapsed_sec * speed_mps

                # Compute projected GPS position at this timestamp
                current_lat, current_lon = project_coordinates(
                    start_lat=start_latitude,
                    start_lon=start_longitude,
                    heading_deg=heading,
                    distance_meters=distance_traveled_m,
                )

                frame_timestamp = (start_time + timedelta(seconds=elapsed_sec)).isoformat()

                # Run YOLOv8 detection on current frame
                results = model(frame, conf=confidence_threshold, imgsz=640, verbose=False)

                if results and len(results) > 0:
                    boxes = results[0].boxes
                    for box in boxes:
                        conf = float(box.conf[0].item())
                        cls_idx = int(box.cls[0].item())
                        class_name = model.names.get(cls_idx, "pothole")
                        xywh = box.xywh[0].tolist()

                        # Construct standard TelemetryPayload
                        payload = TelemetryPayload(
                            device_id=device_id,
                            timestamp=frame_timestamp,
                            hazard_class=class_name,
                            confidence=round(conf, 3),
                            latitude=current_lat,
                            longitude=current_lon,
                            heading=heading,
                            bbox={
                                "x": round(xywh[0], 1),
                                "y": round(xywh[1], 1),
                                "width": round(xywh[2], 1),
                                "height": round(xywh[3], 1),
                            },
                        )

                        # Ingest into database
                        ingested_record = await insert_hazard(payload)

                        detected_hazards.append({
                            "id": ingested_record.get("id"),
                            "frame_number": current_frame_idx,
                            "time_offset_seconds": round(elapsed_sec, 2),
                            "hazard_class": class_name,
                            "confidence": round(conf, 3),
                            "latitude": current_lat,
                            "longitude": current_lon,
                            "bbox": {
                                "x": round(xywh[0], 1),
                                "y": round(xywh[1], 1),
                                "width": round(xywh[2], 1),
                                "height": round(xywh[3], 1),
                            },
                        })

            current_frame_idx += 1

    finally:
        cap.release()

    logger.info(
        f"[VideoProcessor] Video processing finished: {sampled_frames_count} frames evaluated, "
        f"{len(detected_hazards)} potholes detected."
    )

    # Automatically trigger DBSCAN spatial deduplication
    clusters = await run_spatiotemporal_clustering(window_minutes=60)

    is_smooth_road = len(detected_hazards) == 0
    return {
        "status": "success",
        "video_filename": os.path.basename(video_path),
        "video_resolution": f"{video_width}x{video_height}",
        "video_fps": round(video_fps, 1),
        "video_duration_seconds": round(duration_seconds, 2),
        "total_frames_in_video": total_frame_count,
        "sampled_frames_analyzed": sampled_frames_count,
        "potholes_detected_count": len(detected_hazards),
        "is_smooth_road": is_smooth_road,
        "road_condition": "SMOOTH_ROAD" if is_smooth_road else "DAMAGED_ROAD",
        "road_condition_label": "🟢 SMOOTH ROAD (No Potholes)" if is_smooth_road else f"⚠️ {len(detected_hazards)} Pothole(s) Detected",
        "road_surface_quality": "EXCELLENT / GOOD" if is_smooth_road else "HAZARDOUS / CRITICAL",
        "safety_score": 100 if is_smooth_road else max(15, 100 - len(detected_hazards) * 18),
        "clusters_formed_count": len(clusters),
        "clusters": clusters,
        "detections": detected_hazards,
    }
