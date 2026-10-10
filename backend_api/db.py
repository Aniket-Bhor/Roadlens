"""
RoadLens Backend - Database Integration (Supabase + PostGIS)
"""

import os
import uuid
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client
from models import TelemetryPayload

load_dotenv()

logger = logging.getLogger("roadlens.db")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# In-memory local fallback store for local development / testing without active Supabase project
_LOCAL_HAZARDS_STORE: List[Dict[str, Any]] = []
_LOCAL_CLUSTERS_STORE: List[Dict[str, Any]] = []

_client: Optional[Client] = None
_client_initialized: bool = False


def get_supabase_client() -> Optional[Client]:
    """
    Initializes and returns the Supabase client instance.
    Returns None if valid credentials are not configured.
    """
    global _client, _client_initialized
    if _client_initialized:
        return _client

    if not SUPABASE_URL or not SUPABASE_KEY or "your-project" in SUPABASE_URL:
        logger.warning(
            "[DB] Placeholder Supabase credentials detected. Running in mock/local fallback store mode."
        )
        _client = None
        _client_initialized = True
        return None

    try:
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
        _client_initialized = True
        logger.info("[DB] Supabase client initialized successfully.")
        return _client
    except Exception as e:
        logger.error(f"[DB] Failed to initialize Supabase client: {e}")
        _client = None
        _client_initialized = True
        return None


def format_postgis_point(latitude: Optional[float], longitude: Optional[float]) -> Optional[str]:
    """
    Formats WGS84 coordinates into PostGIS WKT geometry string:
    Equivalent to ST_SetSRID(ST_MakePoint(longitude, latitude), 4326).
    Note: PostGIS WKT convention is 'POINT(longitude latitude)'.
    """
    if latitude is None or longitude is None:
        return None
    return f"SRID=4326;POINT({longitude:.7f} {latitude:.7f})"


async def insert_hazard(payload: TelemetryPayload) -> Dict[str, Any]:
    """
    Inserts a validated mobile edge hazard telemetry payload into the PostGIS hazards table.
    Ensures latitude and longitude are formatted for PostGIS geometry (SRID 4326).
    """
    hazard_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    lat = payload.latitude if payload.latitude is not None else 0.0
    lon = payload.longitude if payload.longitude is not None else 0.0
    heading = payload.heading if payload.heading is not None else 0.0
    location_str = format_postgis_point(lat, lon) or "SRID=4326;POINT(0.0000000 0.0000000)"

    record: Dict[str, Any] = {
        "id": hazard_id,
        "device_id": payload.device_id,
        "timestamp": payload.timestamp,
        "hazard_class": payload.hazard_class,
        "confidence": payload.confidence,
        "latitude": lat,
        "longitude": lon,
        "heading": heading,
        "bbox": payload.bbox,
        # PostGIS Geometry column (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326))
        "location": location_str,
        "status": "UNVERIFIED",
        "cluster_id": None,
        "severity_score": payload.confidence,
        "created_at": now_iso,
    }

    client = get_supabase_client()

    if client is not None:
        try:
            # 1. Attempt PostgREST insert into 'hazards' table with timeout safeguard
            response = client.table("hazards").insert(record).execute()
            if response.data and len(response.data) > 0:
                logger.info(f"[DB] Ingested hazard {hazard_id} into Supabase.")
                return response.data[0]
            return record
        except Exception as e:
            logger.error(f"[DB] PostGIS insert failed or timed out: {e}. Falling back to local store.")
            # Store locally if network or PostgREST timeout occurs
            _LOCAL_HAZARDS_STORE.append(record)
            return record
    else:
        # Local mock mode
        _LOCAL_HAZARDS_STORE.append(record)
        logger.debug(f"[DB-Local] Cached hazard {hazard_id} locally.")
        return record


async def fetch_unclustered_hazards(window_minutes: int = 15) -> List[Dict[str, Any]]:
    """
    Fetches unclustered hazard points recorded within the sliding time window.
    """
    cutoff_time = (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).isoformat()
    client = get_supabase_client()

    if client is not None:
        try:
            response = (
                client.table("hazards")
                .select("*")
                .eq("status", "UNVERIFIED")
                .gte("timestamp", cutoff_time)
                .execute()
            )
            return response.data or []
        except Exception as e:
            logger.error(f"[DB] Failed to fetch unclustered hazards: {e}")
            # Fallback to local store
            return [
                h for h in _LOCAL_HAZARDS_STORE
                if h.get("status") == "UNVERIFIED" and h.get("timestamp", "") >= cutoff_time
            ]
    else:
        return [
            h for h in _LOCAL_HAZARDS_STORE
            if h.get("status") == "UNVERIFIED" and h.get("timestamp", "") >= cutoff_time
        ]


async def merge_hazard_cluster(
    cluster_id: str,
    member_ids: List[str],
    center_lat: float,
    center_lon: float,
    aggregate_severity: float,
    hazard_class: str,
) -> Dict[str, Any]:
    """
    Merges duplicate hazard detections into a single 'VERIFIED' aggregate cluster row.
    Updates the member hazard rows to status 'CLUSTERED'.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    client = get_supabase_client()

    cluster_record = {
        "cluster_id": cluster_id,
        "hazard_class": hazard_class,
        "center_latitude": center_lat,
        "center_longitude": center_lon,
        # Center geometry in PostGIS format
        "location": format_postgis_point(center_lat, center_lon),
        "member_count": len(member_ids),
        "aggregate_severity": aggregate_severity,
        "status": "VERIFIED",
        "updated_at": now_iso,
    }

    if client is not None:
        try:
            # 1. Upsert cluster aggregate in 'hazard_clusters' table
            client.table("hazard_clusters").upsert(cluster_record).execute()

            # 2. Update member rows in 'hazards' table
            for member_id in member_ids:
                client.table("hazards").update(
                    {"status": "CLUSTERED", "cluster_id": cluster_id}
                ).eq("id", member_id).execute()

            logger.info(f"[DB] Merged {len(member_ids)} detections into cluster {cluster_id}")
            return cluster_record
        except Exception as e:
            logger.error(f"[DB] Failed to persist cluster merge: {e}")

    # Local fallback
    _LOCAL_CLUSTERS_STORE.append(cluster_record)
    for h in _LOCAL_HAZARDS_STORE:
        if h.get("id") in member_ids:
            h["status"] = "CLUSTERED"
            h["cluster_id"] = cluster_id

    return cluster_record


def check_database_connection() -> Tuple[bool, str]:
    """
    Checks connection health to Supabase / PostGIS.
    Returns (is_connected: bool, mode: str).
    """
    client = get_supabase_client()
    if client is None:
        return False, "local_fallback (configure SUPABASE_URL & SUPABASE_KEY in .env)"

    try:
        # Perform lightweight heartbeat query
        client.table("hazards").select("id").limit(1).execute()
        return True, "supabase_connected"
    except Exception as e:
        return False, f"connection_error: {str(e)}"


async def list_recent_hazards(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Retrieves recent ingested hazards from Supabase or local resilient store.
    """
    client = get_supabase_client()
    if client is not None:
        try:
            res = client.table("hazards").select("*").order("created_at", desc=True).limit(limit).execute()
            return res.data or []
        except Exception as e:
            logger.error(f"[DB] Error listing hazards from Supabase: {e}")
    return list(reversed(_LOCAL_HAZARDS_STORE[-limit:]))


async def list_recent_clusters(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Retrieves recent clustered & verified hazards from Supabase or local resilient store.
    """
    client = get_supabase_client()
    if client is not None:
        try:
            res = client.table("hazard_clusters").select("*").order("updated_at", desc=True).limit(limit).execute()
            return res.data or []
        except Exception as e:
            logger.error(f"[DB] Error listing clusters from Supabase: {e}")
    return list(reversed(_LOCAL_CLUSTERS_STORE[-limit:]))
