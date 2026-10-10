/**
 * RoadLens Scout - Camera & Edge Processing Types
 */

export interface MountingTelemetry {
  pitchDeg: number;
  rollDeg: number;
  heightMeters: number;
  calibrated: boolean;
  alignmentStatus: 'optimal' | 'realign_needed' | 'uncalibrated';
}

export interface VehicleTelemetry {
  latitude: number | null;
  longitude: number | null;
  altitude: number | null;
  speedKmh: number | null;
  headingDeg: number | null;
  cardinal: string;
  hasLocationPermission: boolean;
  gpsLock: boolean;
  isGpsStale: boolean; // True if relying on retained coordinate (e.g. tunnel)
  lastGpsTimestamp: number;
  lastMagTimestamp: number;
}

export interface SynchronizedTelemetrySnapshot {
  latitude: number | null;
  longitude: number | null;
  altitude: number | null;
  speedKmh: number | null;
  headingDeg: number | null;
  cardinal: string;
  isGpsStale: boolean;
  gpsLock: boolean;
  timestampMs: number;
}

export interface ProcessingMetrics {
  currentFps: number;
  targetFps: number;
  frameIntervalMs: number;
  droppedFramesCount: number;
  processedFramesCount: number;
  lastProcessedTimestampMs: number;
  thermalStatus: 'nominal' | 'elevated' | 'throttling';
}

export interface MockDetection {
  id: string;
  label: 'POTHOLE' | 'ROAD_DEBRIS' | 'CONSTRUCTION_BARRIER' | 'CRACK';
  confidence: number;
  boundingBox: {
    top: number;
    left: number;
    width: number;
    height: number;
  };
  projectedDistanceMeters: number;
}
