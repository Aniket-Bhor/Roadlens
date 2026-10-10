-- ==============================================================================
-- RoadLens Step 5: Supabase Webhook Trigger Configuration
-- Target Table: public.hazards
-- Purpose: Dispatches critical verified hazards (Severity >= 4.0) to n8n Webhook
-- ==============================================================================

-- 1. Enable the pg_net extension (Supabase native asynchronous HTTP client)
CREATE EXTENSION IF NOT EXISTS pg_net WITH SCHEMA extensions;

-- 2. Define the Trigger Function to dispatch payloads to n8n
CREATE OR REPLACE FUNCTION public.fn_dispatch_critical_hazard_to_n8n()
RETURNS TRIGGER AS $$
DECLARE
    payload JSONB;
    -- Note: If running n8n locally via Docker/Tunnel, replace with your live webhook URL:
    -- e.g. 'http://host.docker.internal:5678/webhook/hazard-dispatch' or ngrok/cloud URL
    n8n_webhook_url TEXT := 'http://localhost:5678/webhook/hazard-dispatch';
    request_id BIGINT;
BEGIN
    -- Only dispatch when status is 'VERIFIED' AND severity_score is 4 or 5 (Critical)
    IF (NEW.status ILIKE 'VERIFIED') AND (NEW.severity_score >= 4.0) THEN
        
        -- Build the standardized incident payload for n8n triage
        payload := jsonb_build_object(
            'event', TG_OP,
            'id', NEW.id,
            'device_id', NEW.device_id,
            'timestamp', NEW.timestamp,
            'hazard_class', NEW.hazard_class,
            'confidence', NEW.confidence,
            'severity_score', NEW.severity_score,
            'status', NEW.status,
            'latitude', NEW.latitude,
            'longitude', NEW.longitude,
            'heading', NEW.heading,
            'bbox', NEW.bbox,
            'cluster_id', NEW.cluster_id,
            'maps_url', format('https://www.google.com/maps/search/?api=1&query=%s,%s', NEW.latitude, NEW.longitude),
            'dispatched_at', NOW()
        );

        -- Send asynchronous, non-blocking HTTP POST request via pg_net
        SELECT net.http_post(
            url := n8n_webhook_url,
            body := payload,
            headers := '{"Content-Type": "application/json", "User-Agent": "RoadLens-Supabase-Trigger/1.0"}'::jsonb,
            timeout_milliseconds := 5000
        ) INTO request_id;

        RAISE NOTICE 'Critical hazard % (class: %, severity: %) dispatched to n8n (HTTP Request ID: %)',
            NEW.id, NEW.hazard_class, NEW.severity_score, request_id;
            
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 3. Bind the Trigger to the hazards table
DROP TRIGGER IF EXISTS trg_dispatch_critical_hazard ON public.hazards;

CREATE TRIGGER trg_dispatch_critical_hazard
AFTER INSERT OR UPDATE OF status, severity_score
ON public.hazards
FOR EACH ROW
WHEN (NEW.status ILIKE 'VERIFIED' AND NEW.severity_score >= 4.0)
EXECUTE FUNCTION public.fn_dispatch_critical_hazard_to_n8n();

-- 4. Also Bind Trigger to hazard_clusters table (for multi-vehicle verified clusters)
CREATE OR REPLACE FUNCTION public.fn_dispatch_critical_cluster_to_n8n()
RETURNS TRIGGER AS $$
DECLARE
    payload JSONB;
    n8n_webhook_url TEXT := 'http://localhost:5678/webhook/hazard-dispatch';
    request_id BIGINT;
BEGIN
    IF (NEW.status ILIKE 'VERIFIED') AND (NEW.aggregate_severity >= 4.0) THEN
        payload := jsonb_build_object(
            'event', TG_OP,
            'id', NEW.cluster_id,
            'cluster_id', NEW.cluster_id,
            'hazard_class', NEW.hazard_class,
            'confidence', 1.0,
            'severity_score', NEW.aggregate_severity,
            'status', NEW.status,
            'latitude', NEW.center_latitude,
            'longitude', NEW.center_longitude,
            'member_count', NEW.member_count,
            'maps_url', format('https://www.google.com/maps/search/?api=1&query=%s,%s', NEW.center_latitude, NEW.center_longitude),
            'dispatched_at', NOW()
        );

        SELECT net.http_post(
            url := n8n_webhook_url,
            body := payload,
            headers := '{"Content-Type": "application/json", "User-Agent": "RoadLens-Cluster-Trigger/1.0"}'::jsonb,
            timeout_milliseconds := 5000
        ) INTO request_id;

        RAISE NOTICE 'Critical hazard cluster % dispatched to n8n (HTTP Request ID: %)',
            NEW.cluster_id, request_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS trg_dispatch_critical_cluster ON public.hazard_clusters;

CREATE TRIGGER trg_dispatch_critical_cluster
AFTER INSERT OR UPDATE OF status, aggregate_severity
ON public.hazard_clusters
FOR EACH ROW
WHEN (NEW.status ILIKE 'VERIFIED' AND NEW.aggregate_severity >= 4.0)
EXECUTE FUNCTION public.fn_dispatch_critical_cluster_to_n8n();

-- ==============================================================================
-- Verification Query: Check outgoing HTTP request logs in Supabase
-- ==============================================================================
-- SELECT * FROM net._http_response ORDER BY created DESC LIMIT 10;
