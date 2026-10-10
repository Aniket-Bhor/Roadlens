-- ==============================================================================
-- RoadLens PostGIS Database Schema for Supabase (PostgreSQL + PostGIS)
-- Execute this script in the Supabase SQL Editor to initialize the database
-- ==============================================================================

-- 1. Enable PostGIS Extension
CREATE EXTENSION IF NOT EXISTS postgis;

-- 2. Raw Ingested Hazards Table
CREATE TABLE IF NOT EXISTS public.hazards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    hazard_class TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    heading DOUBLE PRECISION NOT NULL,
    bbox JSONB NOT NULL,
    location GEOMETRY(Point, 4326) NOT NULL,
    status TEXT NOT NULL DEFAULT 'UNVERIFIED', -- 'UNVERIFIED', 'CLUSTERED', 'DISMISSED'
    cluster_id TEXT,
    severity_score DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. PostGIS Spatial GIST Index & Fast Temporal Index
CREATE INDEX IF NOT EXISTS hazards_location_idx ON public.hazards USING GIST (location);
CREATE INDEX IF NOT EXISTS hazards_timestamp_idx ON public.hazards (timestamp DESC);
CREATE INDEX IF NOT EXISTS hazards_status_idx ON public.hazards (status);

-- 4. Verified Hazard Clusters Table (Merged Ground Truth)
CREATE TABLE IF NOT EXISTS public.hazard_clusters (
    cluster_id TEXT PRIMARY KEY,
    hazard_class TEXT NOT NULL,
    center_latitude DOUBLE PRECISION NOT NULL,
    center_longitude DOUBLE PRECISION NOT NULL,
    location GEOMETRY(Point, 4326) NOT NULL,
    member_count INTEGER NOT NULL DEFAULT 1,
    aggregate_severity DOUBLE PRECISION NOT NULL,
    status TEXT NOT NULL DEFAULT 'VERIFIED', -- 'VERIFIED', 'RESOLVED', 'FALSE_POSITIVE'
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS hazard_clusters_location_idx ON public.hazard_clusters USING GIST (location);

-- 5. Helper Function for PostGIS Point Generation
CREATE OR REPLACE FUNCTION public.make_hazard_point(lat DOUBLE PRECISION, lon DOUBLE PRECISION)
RETURNS GEOMETRY AS $$
BEGIN
    RETURN ST_SetSRID(ST_MakePoint(lon, lat), 4326);
END;
$$ LANGUAGE plpgsql IMMUTABLE;
