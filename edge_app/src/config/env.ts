/**
 * RoadLens Scout - Environment Configuration
 * Safe parsing and fallback defaults for edge environment variables.
 */

export interface EdgeConfig {
  apiBaseUrl: string;
  wsBaseUrl: string;
  targetFps: number;
  frameIntervalMs: number;
  defaultMountHeightM: number;
  defaultPitchDeg: number;
  offlineQueueMaxSize: number;
  logLevel: 'debug' | 'info' | 'warn' | 'error';
}

export const ENV: EdgeConfig = {
  apiBaseUrl: process.env.EXPO_PUBLIC_API_BASE_URL || 'http://192.168.1.17:8000',
  wsBaseUrl: process.env.EXPO_PUBLIC_WS_BASE_URL || 'ws://192.168.1.17:8000/ws',
  // Strict throttling to 4-6 FPS (default: 5 FPS = 200ms per frame)
  targetFps: Number(process.env.EXPO_PUBLIC_TARGET_FPS) || 5,
  frameIntervalMs: Number(process.env.EXPO_PUBLIC_FRAME_INTERVAL_MS) || 200,
  defaultMountHeightM: Number(process.env.EXPO_PUBLIC_DEFAULT_MOUNT_HEIGHT_M) || 1.25,
  defaultPitchDeg: Number(process.env.EXPO_PUBLIC_DEFAULT_PITCH_DEG) || 12.0,
  offlineQueueMaxSize: Number(process.env.EXPO_PUBLIC_OFFLINE_QUEUE_MAX_SIZE) || 500,
  logLevel: (process.env.EXPO_PUBLIC_LOG_LEVEL as EdgeConfig['logLevel']) || 'debug',
};
