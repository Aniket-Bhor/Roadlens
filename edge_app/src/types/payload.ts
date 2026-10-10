/**
 * RoadLens Scout - Edge Hazard & Queue Payload Contract
 */

export interface BoundingBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export type HazardClass = 'pothole' | 'debris' | 'crack' | 'barrier' | 'flood';

/**
 * Agreed JSON contract for downstream n8n and FastAPI ingestion.
 */
export interface RoadHazardPayload {
  device_id: string;
  timestamp: number;
  hazard_class: HazardClass;
  confidence: number;
  latitude: number | null;
  longitude: number | null;
  heading: number | null;
  bbox: BoundingBox;
}

export interface ExtractedDetection {
  hazard_class: HazardClass;
  confidence: number;
  bbox: BoundingBox;
}
