"""
RoadLens Backend - Step 4 Verification & Integration Test Suite
Tests FastAPI Ingestion, PostGIS contract validation, and DBSCAN spatiotemporal clustering.
"""

import asyncio
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from main import app
from db import _LOCAL_HAZARDS_STORE, _LOCAL_CLUSTERS_STORE
from clustering import run_spatiotemporal_clustering

client = TestClient(app)


def test_health_check():
    """Verify GET /health returns 200 OK and healthy status."""
    response = client.get("/health")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data
    assert "db_mode" in data
    print("[PASS] test_health_check passed!")


def test_telemetry_ingest_contract():
    """Verify POST /api/v1/telemetry validates the exact agreed JSON contract."""
    payload = {
        "device_id": "scout-edge-unit-01",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "hazard_class": "pothole",
        "confidence": 0.92,
        "latitude": 19.076000,
        "longitude": 72.877700,
        "heading": 135.0,
        "bbox": [96.0, 140.0, 128.0, 64.0],
    }

    response = client.post("/api/v1/telemetry", json=payload)
    assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.text}"
    data = response.json()
    assert data["status"] == "received"
    assert data["device_id"] == "scout-edge-unit-01"
    assert data["hazard_class"] == "pothole"
    assert data["hazard_id"] is not None
    print("[PASS] test_telemetry_ingest_contract passed!")


def test_telemetry_dict_bbox_and_epoch_timestamp():
    """Verify edge client sending bbox as object {x, y, width, height} and epoch timestamp."""
    payload = {
        "device_id": "scout-mobile-phone-01",
        "timestamp": 1728514800000,
        "hazard_class": "flood",
        "confidence": 0.89,
        "latitude": 19.076010,
        "longitude": 72.877710,
        "heading": 134.5,
        "bbox": {"x": 100.0, "y": 150.0, "width": 120.0, "height": 60.0},
    }

    response = client.post("/api/v1/telemetry", json=payload)
    assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.text}"
    data = response.json()
    assert data["status"] == "received"
    assert data["hazard_class"] == "flood"
    print("[PASS] test_telemetry_dict_bbox_and_epoch_timestamp passed!")


def test_telemetry_validation_rejection():
    """Verify invalid payloads (e.g. confidence > 1.0) are rejected with 422."""
    invalid_payload = {
        "device_id": "scout-edge-unit-02",
        "timestamp": "invalid",
        "hazard_class": "pothole",
        "confidence": 1.5,  # Invalid: > 1.0
        "latitude": 19.076000,
        "longitude": 72.877700,
        "heading": 135.0,
        "bbox": [10.0, 20.0],  # Invalid: needs 4 items
    }
    response = client.post("/api/v1/telemetry", json=invalid_payload)
    assert response.status_code == 422, f"Expected 422, got {response.status_code}"
    print("[PASS] test_telemetry_validation_rejection passed!")


def test_dbscan_spatial_clustering():
    """
    Verify DBSCAN merges 2 reports within 5 meters into a single verified cluster.
    Point 1: 19.076000, 72.877700
    Point 2: 19.076020, 72.877715 (~2.7 meters apart)
    """
    # Report 1 from Car A
    payload_car_a = {
        "device_id": "car-scout-alpha",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "hazard_class": "pothole",
        "confidence": 0.88,
        "latitude": 19.076000,
        "longitude": 72.877700,
        "heading": 135.0,
        "bbox": [96.0, 140.0, 128.0, 64.0],
    }

    # Report 2 from Car B (~2.7m away, reported 2 minutes later)
    payload_car_b = {
        "device_id": "car-scout-bravo",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "hazard_class": "pothole",
        "confidence": 0.94,
        "latitude": 19.076020,
        "longitude": 72.877715,
        "heading": 138.0,
        "bbox": [100.0, 142.0, 124.0, 60.0],
    }

    client.post("/api/v1/telemetry", json=payload_car_a)
    client.post("/api/v1/telemetry", json=payload_car_b)

    # Verify the background clustering task merged the two reports into a single verified cluster
    assert len(_LOCAL_CLUSTERS_STORE) >= 1, f"Expected at least 1 cluster in store, got {len(_LOCAL_CLUSTERS_STORE)}"
    cluster = _LOCAL_CLUSTERS_STORE[-1]
    assert cluster["status"] == "VERIFIED"
    assert cluster["hazard_class"] == "pothole"
    assert cluster["member_count"] >= 2
    # Verify centroid is calculated accurately (mean of 19.076000 and 19.076020)
    assert 19.0759 < cluster["center_latitude"] < 19.0761
    assert 72.8776 < cluster["center_longitude"] < 72.8778
    # Verify severity was aggregated
    assert cluster["aggregate_severity"] > 1.0
    print(f"[PASS] test_dbscan_spatial_clustering passed! Formed cluster: {cluster['cluster_id']} (Members: {cluster['member_count']}, Severity: {cluster['aggregate_severity']})")


def test_inspection_endpoints():
    """Verify GET /api/v1/hazards and GET /api/v1/clusters."""
    res_h = client.get("/api/v1/hazards")
    assert res_h.status_code == 200
    assert "hazards" in res_h.json()

    res_c = client.get("/api/v1/clusters")
    assert res_c.status_code == 200
    assert "clusters" in res_c.json()
    print("[PASS] test_inspection_endpoints passed!")


def test_weather_road_quality_prediction():
    """Verify GET /api/v1/analytics/road-quality calculates weather degradation & RQI."""
    res_rain = client.get("/api/v1/analytics/road-quality?weather=rain&speed_kmh=40")
    assert res_rain.status_code == 200
    data_rain = res_rain.json()
    assert "road_quality_index" in data_rain
    assert data_rain["hydroplaning_risk_pct"] > 30.0
    assert data_rain["pothole_danger_multiplier"] >= 2.0

    res_clear = client.get("/api/v1/analytics/road-quality?weather=clear&speed_kmh=40")
    assert res_clear.status_code == 200
    data_clear = res_clear.json()
    assert data_clear["road_quality_index"] > data_rain["road_quality_index"]
    print(f"[PASS] test_weather_road_quality_prediction passed! (Clear RQI: {data_clear['road_quality_index']}, Rain RQI: {data_rain['road_quality_index']})")


def test_video_upload_processing():
    """Verify POST /api/v1/videos/process accepts video, runs YOLOv8 model, and returns report."""
    import os
    import tempfile
    import numpy as np
    import cv2

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        tmp_video_path = f.name

    try:
        # Create a small 10-frame synthetic test video
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out = cv2.VideoWriter(tmp_video_path, fourcc, 10.0, (320, 240))
        for _ in range(10):
            frame = np.zeros((240, 320, 3), dtype=np.uint8)
            frame[:] = (70, 70, 70)
            cv2.circle(frame, (160, 120), 25, (15, 15, 15), -1)
            out.write(frame)
        out.release()

        with open(tmp_video_path, "rb") as video_file:
            res = client.post(
                "/api/v1/videos/process",
                files={"video": ("test_road.mp4", video_file, "video/mp4")},
                data={
                    "device_id": "test-dashcam",
                    "latitude": "18.464928",
                    "longitude": "73.836153",
                    "heading": "348.0",
                    "speed_kmh": "30.0",
                    "confidence_threshold": "0.30",
                },
            )
            assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
            data = res.json()
            assert data["status"] == "success"
            assert "sampled_frames_analyzed" in data
            assert "potholes_detected_count" in data
            print(f"[PASS] test_video_upload_processing passed! Analyzed {data['sampled_frames_analyzed']} frames, found {data['potholes_detected_count']} potholes.")
    finally:
        if os.path.exists(tmp_video_path):
            os.remove(tmp_video_path)


if __name__ == "__main__":
    print("--- Running Step 4 Automated Test Suite ---")
    test_health_check()
    test_telemetry_ingest_contract()
    test_telemetry_dict_bbox_and_epoch_timestamp()
    test_telemetry_validation_rejection()
    test_dbscan_spatial_clustering()
    test_inspection_endpoints()
    test_weather_road_quality_prediction()
    test_video_upload_processing()
    print("--- ALL TESTS PASSED SUCCESSFULLY! ---")
