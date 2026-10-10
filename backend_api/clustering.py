"""
RoadLens Backend - Spatiotemporal Clustering Engine (DBSCAN)
Spatial deduplication to merge multi-vehicle hazard reports into verified ground-truth clusters.
"""

import os
import uuid
import logging
import numpy as np
import pandas as pd
from typing import List, Dict, Any
from datetime import datetime, timezone, timedelta
from sklearn.cluster import DBSCAN
from db import fetch_unclustered_hazards, merge_hazard_cluster

logger = logging.getLogger("roadlens.clustering")

# Earth radius in meters
EARTH_RADIUS_METERS = 6371000.0

# 5-meter clustering radius converted to haversine radians
DBSCAN_RADIUS_METERS = float(os.getenv("DBSCAN_EPS_METERS", "5.0"))
DBSCAN_EPS_RADIANS = DBSCAN_RADIUS_METERS / EARTH_RADIUS_METERS

# 15-minute sliding time window for spatiotemporal grouping
TIME_WINDOW_MINUTES = int(os.getenv("DBSCAN_TIME_WINDOW_MINUTES", "15"))
MIN_CLUSTER_SAMPLES = int(os.getenv("DBSCAN_MIN_SAMPLES", "2"))


def parse_timestamp_to_epoch(ts: Any) -> float:
    """
    Parses string or numeric timestamp to epoch seconds.
    """
    if isinstance(ts, (int, float)):
        return float(ts / 1000.0 if ts > 1e11 else ts)
    if isinstance(ts, str):
        try:
            # Handle ISO-8601 strings
            clean_ts = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_ts)
            return dt.timestamp()
        except Exception:
            return datetime.now(timezone.utc).timestamp()
    return datetime.now(timezone.utc).timestamp()


async def run_spatiotemporal_clustering(window_minutes: int = TIME_WINDOW_MINUTES) -> List[Dict[str, Any]]:
    """
    Executes DBSCAN spatial deduplication on recent unclustered hazard detections.
    
    1. Fetches candidate detections from the sliding time window.
    2. Groups candidates by hazard class.
    3. Runs DBSCAN with haversine metric using 5-meter epsilon (in radians).
    4. Computes centroid coordinates and aggregate severity scores.
    5. Merges duplicates in PostGIS/Supabase into a single 'VERIFIED' hazard cluster.
    
    Returns a list of formed/updated cluster records.
    """
    unclustered_hazards = await fetch_unclustered_hazards(window_minutes=window_minutes)

    if not unclustered_hazards or len(unclustered_hazards) < MIN_CLUSTER_SAMPLES:
        logger.debug(
            f"[Clustering] Insufficient candidates ({len(unclustered_hazards)}) for clustering. Minimum required: {MIN_CLUSTER_SAMPLES}"
        )
        return []

    df = pd.DataFrame(unclustered_hazards)
    formed_clusters: List[Dict[str, Any]] = []

    # Group candidates by hazard class (e.g. 'pothole', 'debris', 'crack')
    for hazard_class, class_group in df.groupby("hazard_class"):
        if len(class_group) < MIN_CLUSTER_SAMPLES:
            continue

        # Extract coordinates in radians for haversine metric: [latitude, longitude]
        coords_deg = class_group[["latitude", "longitude"]].values
        coords_rad = np.radians(coords_deg)

        # Configure DBSCAN with 5-meter radius epsilon (in radians)
        db = DBSCAN(
            eps=DBSCAN_EPS_RADIANS,
            min_samples=MIN_CLUSTER_SAMPLES,
            metric="haversine",
            algorithm="ball_tree",
        )
        labels = db.fit_predict(coords_rad)

        # Process detected clusters (labels >= 0 represent valid cluster cores/borders)
        unique_labels = set(labels)
        for label in unique_labels:
            if label == -1:
                # -1 denotes noise (individual standalone detections pending verification)
                continue

            # Identify member detections belonging to this cluster
            member_indices = np.where(labels == label)[0]
            member_df = class_group.iloc[member_indices]
            member_ids = member_df["id"].tolist()

            # Calculate cluster centroid (mean latitude and mean longitude)
            center_lat = float(member_df["latitude"].mean())
            center_lon = float(member_df["longitude"].mean())

            # Calculate aggregate severity score:
            # Base confidence sum scaled by frequency factor
            base_confidence_sum = float(member_df["confidence"].sum())
            member_count = len(member_df)
            # Aggregate score formula: sum of confidences + multi-vehicle confirmation bonus
            aggregate_severity = round(base_confidence_sum + (member_count - 1) * 0.25, 3)

            cluster_id = f"cluster-{hazard_class}-{uuid.uuid4().hex[:8]}"

            # Update database: merge member points into a single 'VERIFIED' aggregate
            cluster_record = await merge_hazard_cluster(
                cluster_id=cluster_id,
                member_ids=member_ids,
                center_lat=center_lat,
                center_lon=center_lon,
                aggregate_severity=aggregate_severity,
                hazard_class=str(hazard_class),
            )

            formed_clusters.append(cluster_record)
            logger.info(
                f"[Clustering] Formed {hazard_class} cluster {cluster_id} with {member_count} detections at ({center_lat:.6f}, {center_lon:.6f}) - Severity: {aggregate_severity}"
            )

    return formed_clusters
