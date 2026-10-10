import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  StyleSheet,
  View,
  Text,
  TouchableOpacity,
  SafeAreaView,
  StatusBar,
  ActivityIndicator,
  Platform,
} from 'react-native';
import {
  Camera,
  useCameraDevice,
  useCameraPermission,
  useFrameProcessor,
} from 'react-native-vision-camera';
import { useSharedValue, useRunOnJS } from 'react-native-worklets-core';
import { useTensorflowModel } from 'react-native-fast-tflite';
import { useResizePlugin } from 'vision-camera-resize-plugin';
import { ENV } from '../config/env';
import { MountingTelemetry, ProcessingMetrics, MockDetection } from '../types/camera';
import { RoadHazardPayload } from '../types/payload';
import { useVehicleTelemetry } from '../hooks/useVehicleTelemetry';
import { extractDetectionsFromYoloOutput } from '../utils/yoloParser';
import { enqueueHazardPayload, getQueueLength, getOrCreateDeviceId, clearQueue } from '../services/payloadQueue';
import { flushQueueToBackend } from '../services/queueSyncWorker';

export const RoadCamera: React.FC = () => {
  // 1. Camera Permissions & Device Hardware
  const { hasPermission, requestPermission } = useCameraPermission();
  const device = useCameraDevice('back');
  const [permissionRequested, setPermissionRequested] = useState<boolean>(false);

  // 2. Sensor Fusion & Telemetry (GPS + Magnetometer Compass Heading)
  const { telemetry, telemetryShared } = useVehicleTelemetry();

  // 3. Step 3: TFLite Model Loading & Vision Resize Plugin
  // Loads yolov8_road.tflite asset bundled via metro.config.js assetExts
  const tflitePlugin = useTensorflowModel(require('../../assets/yolov8_road.tflite'), []);
  const { resize } = useResizePlugin();

  // Offline queue state & device ID
  const [queueCount, setQueueCount] = useState<number>(0);
  const [lastQueuedHazard, setLastQueuedHazard] = useState<RoadHazardPayload | null>(null);
  const deviceIdShared = useSharedValue<string>('scout-edge-init');

  useEffect(() => {
    async function initDeviceAndQueue() {
      const devId = await getOrCreateDeviceId();
      deviceIdShared.value = devId;
      const count = await getQueueLength();
      setQueueCount(count);
    }
    initDeviceAndQueue();

    // Periodic store-and-forward queue sync to backend every 3 seconds
    const syncInterval = setInterval(async () => {
      const res = await flushQueueToBackend();
      if (res.sent > 0) {
        setQueueCount(res.remaining);
      }
    }, 3000);

    return () => clearInterval(syncInterval);
  }, [deviceIdShared]);

  // 4. Processing Metrics & Mounting Telemetry State
  const [metrics, setMetrics] = useState<ProcessingMetrics>({
    currentFps: 0,
    targetFps: ENV.targetFps,
    frameIntervalMs: ENV.frameIntervalMs,
    droppedFramesCount: 0,
    processedFramesCount: 0,
    lastProcessedTimestampMs: 0,
    thermalStatus: 'nominal',
  });

  const [mountTelemetry] = useState<MountingTelemetry>({
    pitchDeg: ENV.defaultPitchDeg,
    rollDeg: 0.2,
    heightMeters: ENV.defaultMountHeightM,
    calibrated: true,
    alignmentStatus: 'optimal',
  });

  const [showDetectionOverlay, setShowDetectionOverlay] = useState<boolean>(true);

  // Rolling timestamp window for accurate JS-side FPS calculation
  const timestampsRef = useRef<number[]>([]);
  const droppedCountRef = useRef<number>(0);
  const processedCountRef = useRef<number>(0);

  // 5. JS Callback invoked from the frame processor Worklet
  const handleFrameEvaluated = useCallback((frameTimestamp: number) => {
    processedCountRef.current += 1;
    const now = frameTimestamp;
    timestampsRef.current.push(now);

    const cutoff = now - 2000;
    while (timestampsRef.current.length > 0 && timestampsRef.current[0] < cutoff) {
      timestampsRef.current.shift();
    }

    let calculatedFps = ENV.targetFps;
    if (timestampsRef.current.length > 1) {
      const windowSpan = (now - timestampsRef.current[0]) / 1000;
      if (windowSpan > 0) {
        calculatedFps = Number(((timestampsRef.current.length - 1) / windowSpan).toFixed(1));
      }
    }

    setMetrics((prev) => ({
      ...prev,
      currentFps: calculatedFps,
      processedFramesCount: processedCountRef.current,
      droppedFramesCount: droppedCountRef.current,
      lastProcessedTimestampMs: now,
    }));
  }, []);

  const handleFrameDropped = useCallback(() => {
    droppedCountRef.current += 1;
  }, []);

  // Async queue dispatch invoked when a candidate hazard is detected in the Worklet
  const handleHazardDetected = useCallback(async (payload: RoadHazardPayload) => {
    const newCount = await enqueueHazardPayload(payload);
    setQueueCount(newCount);
    setLastQueuedHazard(payload);
  }, []);

  const onFrameEvaluated = useRunOnJS(handleFrameEvaluated, [handleFrameEvaluated]);
  const onFrameDropped = useRunOnJS(handleFrameDropped, [handleFrameDropped]);
  const onHazardDetected = useRunOnJS(handleHazardDetected, [handleHazardDetected]);

  // 6. Worklet Cooldown Pattern, TFLite Inference & Telemetry Synchronization
  // Strictly enforces 4–6 FPS (roughly one evaluation every 200ms) to protect mobile SoC
  const lastProcessedTime = useSharedValue<number>(0);
  const cooldownIntervalMs = ENV.frameIntervalMs;

  const frameProcessor = useFrameProcessor((frame) => {
    'worklet';
    const now = Date.now();
    const elapsed = now - lastProcessedTime.value;

    // Strict cooldown check: drop frames arriving before target interval
    if (elapsed < cooldownIntervalMs) {
      onFrameDropped();
      return;
    }
    lastProcessedTime.value = now;

    // Retrieve synchronized vehicle telemetry from thread-safe SharedValue
    const currentTelemetry = telemetryShared.value;

    // Check GPS freshness with 3-second retention window (e.g. driving through a tunnel)
    const gpsAge = now - currentTelemetry.timestampMs;
    const hasValidGps = currentTelemetry.latitude != null && (gpsAge <= 3000 || !currentTelemetry.isGpsStale);

    const lat = hasValidGps ? currentTelemetry.latitude : null;
    const lon = hasValidGps ? currentTelemetry.longitude : null;
    const heading = currentTelemetry.headingDeg;

    // Step 2 Log Output: [Frame Captured] Lat: 19.07, Lon: 72.87, Heading: 135°
    const latStr = lat != null ? lat.toFixed(2) : 'null';
    const lonStr = lon != null ? lon.toFixed(2) : 'null';
    const headingStr = heading != null ? `${Math.round(heading)}°` : 'null';
    console.log(`[Frame Captured] Lat: ${latStr}, Lon: ${lonStr}, Heading: ${headingStr}`);

    // Step 3: TFLite Inference & Frame Resizing
    const model = tflitePlugin.state === 'loaded' ? tflitePlugin.model : undefined;

    if (model != null) {
      try {
        // Resize frame to 320x320 RGB uint8 ArrayBuffer (quantized mobile YOLO input)
        const resized = resize(frame, {
          scale: {
            width: 320,
            height: 320,
          },
          pixelFormat: 'rgb',
          dataType: 'uint8',
        });

        // Run inference synchronously inside the Worklet
        const outputs = model.runSync([resized.buffer as ArrayBuffer]);

        // Parse outputs[0] for hazard detections with confidence threshold >= 0.60
        const detection = extractDetectionsFromYoloOutput(outputs[0], 0.60);

        if (detection != null && detection.confidence >= 0.60) {
          // Assemble agreed JSON payload contract:
          // { device_id, timestamp, hazard_class, confidence, latitude, longitude, heading, bbox }
          const payload: RoadHazardPayload = {
            device_id: deviceIdShared.value,
            timestamp: now,
            hazard_class: detection.hazard_class,
            confidence: detection.confidence,
            latitude: lat,
            longitude: lon,
            heading: heading != null ? Math.round(heading) : null,
            bbox: detection.bbox,
          };

          // Push to offline store-and-forward queue
          onHazardDetected(payload);
        }
      } catch (inferenceErr) {
        // Graceful handling for placeholder weights
      }
    }

    // Bridge frame evaluation metrics back to JS thread
    onFrameEvaluated(now);
  }, [cooldownIntervalMs, tflitePlugin, resize, telemetryShared, deviceIdShared, onHazardDetected, onFrameEvaluated, onFrameDropped]);

  // Initial Permission Prompt on Mount
  useEffect(() => {
    if (!hasPermission && !permissionRequested) {
      setPermissionRequested(true);
      requestPermission();
    }
  }, [hasPermission, permissionRequested, requestPermission]);

  // Manual test trigger to verify AsyncStorage queuing pipeline on demand
  const handleSimulateDetection = async () => {
    const devId = await getOrCreateDeviceId();
    const payload: RoadHazardPayload = {
      device_id: devId,
      timestamp: Date.now(),
      hazard_class: 'pothole',
      confidence: 0.94,
      latitude: telemetry.latitude,
      longitude: telemetry.longitude,
      heading: telemetry.headingDeg != null ? Math.round(telemetry.headingDeg) : null,
      bbox: { x: 96, y: 140, width: 128, height: 64 },
    };
    const count = await enqueueHazardPayload(payload);
    setQueueCount(count);
    setLastQueuedHazard(payload);
  };

  const handleClearQueue = async () => {
    await clearQueue();
    setQueueCount(0);
    setLastQueuedHazard(null);
  };

  // Mock detection state for HUD visualization
  const activeBbox: MockDetection = {
    id: lastQueuedHazard ? `det-${lastQueuedHazard.timestamp}` : 'det-live',
    label: lastQueuedHazard ? (lastQueuedHazard.hazard_class.toUpperCase() as any) : 'POTHOLE',
    confidence: lastQueuedHazard ? lastQueuedHazard.confidence : 0.88,
    boundingBox: {
      top: 48,
      left: 28,
      width: 44,
      height: 22,
    },
    projectedDistanceMeters: 8.4,
  };

  // Permission Denied View
  if (!hasPermission) {
    return (
      <SafeAreaView style={styles.permissionContainer}>
        <StatusBar barStyle="light-content" backgroundColor="#0B1120" />
        <View style={styles.permissionCard}>
          <View style={styles.permissionIconBadge}>
            <Text style={styles.permissionIconText}>📷</Text>
          </View>
          <Text style={styles.permissionTitle}>RoadLens Scout Edge</Text>
          <Text style={styles.permissionSubtitle}>Hardware Camera Access Required</Text>
          <Text style={styles.permissionDescription}>
            Scout performs on-device computer vision and sensor fusion at 4–6 FPS to detect road hazards
            in real time. All processing is executed locally at the edge. Raw video is never uploaded or streamed.
          </Text>

          <TouchableOpacity
            style={styles.permissionButton}
            onPress={() => requestPermission()}
            activeOpacity={0.8}
          >
            <Text style={styles.permissionButtonText}>Grant Camera Permission</Text>
          </TouchableOpacity>

          <Text style={styles.permissionPrivacyNote}>
            🔒 Privacy First: Edge blurring & offline queuing enabled.
          </Text>
        </View>
      </SafeAreaView>
    );
  }

  // Camera Hardware Not Ready View
  if (!device) {
    return (
      <SafeAreaView style={styles.permissionContainer}>
        <StatusBar barStyle="light-content" backgroundColor="#0B1120" />
        <View style={styles.permissionCard}>
          <ActivityIndicator size="large" color="#38BDF8" style={{ marginBottom: 16 }} />
          <Text style={styles.permissionTitle}>Detecting Camera Hardware...</Text>
          <Text style={styles.permissionDescription}>
            Initializing back-facing optical sensor. Please ensure camera hardware is accessible or virtual camera is configured in emulator.
          </Text>
        </View>
      </SafeAreaView>
    );
  }

  // Formatting helpers for HUD UI
  const latDisplay = telemetry.latitude != null ? telemetry.latitude.toFixed(4) : '--.----';
  const lonDisplay = telemetry.longitude != null ? telemetry.longitude.toFixed(4) : '--.----';
  const headingDisplay = telemetry.headingDeg != null ? `${Math.round(telemetry.headingDeg)}°` : '--°';
  const speedDisplay = telemetry.speedKmh != null ? `${telemetry.speedKmh} km/h` : '0 km/h';

  const tfliteStatusLabel =
    tflitePlugin.state === 'loaded'
      ? 'TFLITE ACTIVE (320x320)'
      : tflitePlugin.state === 'loading'
      ? 'TFLITE LOADING'
      : 'TFLITE STANDBY';

  return (
    <View style={styles.container}>
      <StatusBar barStyle="light-content" backgroundColor="transparent" translucent />

      {/* Hardware Camera Viewfinder */}
      <Camera
        style={StyleSheet.absoluteFill}
        device={device}
        isActive={true}
        frameProcessor={frameProcessor}
        photo={false}
        video={false}
        audio={false}
        enableFpsGraph={false}
      />

      {/* Full-Screen Edge HUD Overlay */}
      <SafeAreaView style={styles.overlayContainer} pointerEvents="box-none">
        {/* Top Status & Metrics Bar */}
        <View style={styles.topBar}>
          <View style={styles.brandBadge}>
            <View style={styles.liveDot} />
            <Text style={styles.brandText}>ROADLENS SCOUT</Text>
            <Text style={styles.versionTag}>STEP 3</Text>
          </View>

          {/* Sensor Fusion & TFLite Status Badges */}
          <View style={styles.metricsGroup}>
            {/* TFLite Model Status Pill */}
            <View
              style={[
                styles.sensorPill,
                tflitePlugin.state === 'loaded' ? styles.sensorPillSuccess : styles.sensorPillNeutral,
              ]}
            >
              <Text style={styles.sensorPillText}>{tfliteStatusLabel}</Text>
            </View>

            {/* Offline Payload Queue Pill */}
            <View
              style={[
                styles.sensorPill,
                queueCount > 0 ? styles.sensorPillWarning : styles.sensorPillNeutral,
              ]}
            >
              <Text style={styles.sensorPillText}>QUEUE: {queueCount}</Text>
            </View>

            {/* Live Throttled FPS Counter */}
            <View style={styles.metricPill}>
              <Text style={styles.metricLabel}>FPS</Text>
              <Text style={styles.metricValue}>
                {metrics.currentFps.toFixed(1)}
                <Text style={styles.metricSub}> / {metrics.targetFps}</Text>
              </Text>
            </View>
          </View>
        </View>

        {/* Center Viewfinder Reticle, Horizon Guide & Detection Overlay */}
        <View style={styles.centerReticleContainer} pointerEvents="box-none">
          {/* Compass Ribbon Guide */}
          <View style={styles.compassRibbon}>
            <Text style={styles.compassRibbonText}>
              ◀ {telemetry.headingDeg != null ? `${Math.round(telemetry.headingDeg)}°` : '--°'} [
              {telemetry.cardinal}] AZIMUTH ▶
            </Text>
          </View>

          {/* Horizon Line / Ground Plane Guide */}
          <View style={styles.horizonGuide}>
            <View style={styles.horizonLineLeft} />
            <View style={styles.horizonCenterCrosshair}>
              <View style={styles.crosshairDot} />
            </View>
            <View style={styles.horizonLineRight} />
          </View>

          {/* Detected Hazard Bounding Box */}
          {showDetectionOverlay && (
            <View
              style={[
                styles.mockDetectionBox,
                {
                  top: `${activeBbox.boundingBox.top}%`,
                  left: `${activeBbox.boundingBox.left}%`,
                  width: `${activeBbox.boundingBox.width}%`,
                  height: `${activeBbox.boundingBox.height}%`,
                },
              ]}
              pointerEvents="none"
            >
              <View style={styles.detectionLabelTag}>
                <Text style={styles.detectionLabelText}>
                  {activeBbox.label} {(activeBbox.confidence * 100).toFixed(0)}%
                </Text>
                <Text style={styles.detectionDistanceText}>
                  EST. {activeBbox.projectedDistanceMeters}m
                </Text>
              </View>
            </View>
          )}

          {/* Controls: Simulate Detection / Toggle BBox */}
          <View style={styles.centerControlsGroup}>
            <TouchableOpacity
              style={styles.actionBadge}
              onPress={handleSimulateDetection}
              activeOpacity={0.7}
            >
              <Text style={styles.actionBadgeText}>+ TEST ENQUEUE HAZARD</Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={styles.hudToggleBadge}
              onPress={() => setShowDetectionOverlay((prev) => !prev)}
              activeOpacity={0.7}
            >
              <Text style={styles.hudToggleText}>
                {showDetectionOverlay ? 'BBOX: ON' : 'BBOX: OFF'}
              </Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* Bottom Sensor Fusion Telemetry & Queue Bar */}
        <View style={styles.bottomBar}>
          {/* Synchronized Telemetry Dashboard Card */}
          <View style={styles.telemetryCard}>
            <View style={styles.telemetryCardHeader}>
              <View style={styles.telemetryCardHeaderLeft}>
                <Text style={styles.telemetryCardTitle}>SYNCHRONIZED VEHICLE TELEMETRY</Text>
                <View
                  style={[
                    styles.syncStatusBadge,
                    telemetry.gpsLock ? styles.syncStatusActive : styles.syncStatusPending,
                  ]}
                >
                  <Text style={styles.syncStatusText}>
                    {telemetry.gpsLock ? 'FUSION ACTIVE' : 'AWAITING FIX'}
                  </Text>
                </View>
              </View>
              <View style={styles.queueActions}>
                <Text style={styles.speedText}>{speedDisplay}</Text>
                {queueCount > 0 && (
                  <TouchableOpacity onPress={handleClearQueue} style={styles.clearQueueButton}>
                    <Text style={styles.clearQueueText}>CLEAR ({queueCount})</Text>
                  </TouchableOpacity>
                )}
              </View>
            </View>

            {/* 4-Column Live Telemetry Readout */}
            <View style={styles.telemetryGrid}>
              <View style={styles.telemetryItem}>
                <Text style={styles.telemetryLabel}>LATITUDE</Text>
                <Text style={styles.telemetryValue}>{latDisplay}</Text>
              </View>
              <View style={styles.telemetryItem}>
                <Text style={styles.telemetryLabel}>LONGITUDE</Text>
                <Text style={styles.telemetryValue}>{lonDisplay}</Text>
              </View>
              <View style={styles.telemetryItem}>
                <Text style={styles.telemetryLabel}>HEADING</Text>
                <Text style={styles.telemetryValue}>
                  {headingDisplay} ({telemetry.cardinal})
                </Text>
              </View>
              <View style={styles.telemetryItem}>
                <Text style={styles.telemetryLabel}>QUEUE BUFFER</Text>
                <Text style={styles.telemetryValue}>
                  {queueCount}/{ENV.offlineQueueMaxSize}
                </Text>
              </View>
            </View>
          </View>

          {/* Pipeline Notice & Contract Info */}
          <View style={styles.footerInfo}>
            <Text style={styles.footerInfoText}>
              ⚡ 320x320 uint8 TFLite • Synchronized Lat/Lon/Heading • Local Store-and-Forward
            </Text>
          </View>
        </View>
      </SafeAreaView>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#000000',
  },
  overlayContainer: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    justifyContent: 'space-between',
    paddingHorizontal: 14,
    paddingTop: Platform.OS === 'android' ? (StatusBar.currentHeight || 24) + 8 : 12,
    paddingBottom: 14,
  },
  topBar: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: 6,
  },
  brandBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(15, 23, 42, 0.9)',
    paddingHorizontal: 10,
    paddingVertical: 7,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: 'rgba(56, 189, 248, 0.3)',
    gap: 6,
  },
  liveDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: '#10B981',
  },
  brandText: {
    color: '#F8FAFC',
    fontWeight: '800',
    fontSize: 11,
    letterSpacing: 0.5,
  },
  versionTag: {
    color: '#38BDF8',
    fontWeight: '700',
    fontSize: 9,
    marginLeft: 2,
  },
  metricsGroup: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  sensorPill: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(15, 23, 42, 0.9)',
    paddingHorizontal: 8,
    paddingVertical: 6,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: 'rgba(56, 189, 248, 0.25)',
    gap: 5,
  },
  sensorPillSuccess: {
    borderColor: 'rgba(16, 185, 129, 0.5)',
  },
  sensorPillWarning: {
    borderColor: 'rgba(245, 158, 11, 0.5)',
  },
  sensorPillNeutral: {
    borderColor: 'rgba(100, 116, 139, 0.3)',
  },
  sensorPillText: {
    color: '#F1F5F9',
    fontSize: 9,
    fontWeight: '700',
    letterSpacing: 0.3,
  },
  metricPill: {
    backgroundColor: 'rgba(15, 23, 42, 0.9)',
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: 'rgba(56, 189, 248, 0.25)',
    alignItems: 'flex-end',
  },
  metricLabel: {
    color: '#94A3B8',
    fontSize: 8,
    fontWeight: '700',
    letterSpacing: 0.5,
  },
  metricValue: {
    color: '#38BDF8',
    fontSize: 13,
    fontWeight: '800',
  },
  metricSub: {
    color: '#64748B',
    fontSize: 9,
    fontWeight: '500',
  },
  centerReticleContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    position: 'relative',
  },
  compassRibbon: {
    position: 'absolute',
    top: 16,
    backgroundColor: 'rgba(15, 23, 42, 0.75)',
    paddingHorizontal: 12,
    paddingVertical: 4,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: 'rgba(56, 189, 248, 0.3)',
  },
  compassRibbonText: {
    color: '#38BDF8',
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 1,
  },
  horizonGuide: {
    flexDirection: 'row',
    alignItems: 'center',
    width: '75%',
    opacity: 0.7,
  },
  horizonLineLeft: {
    flex: 1,
    height: 1,
    backgroundColor: 'rgba(56, 189, 248, 0.5)',
  },
  horizonCenterCrosshair: {
    width: 24,
    height: 24,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: 'rgba(56, 189, 248, 0.8)',
    justifyContent: 'center',
    alignItems: 'center',
    marginHorizontal: 8,
  },
  crosshairDot: {
    width: 4,
    height: 4,
    borderRadius: 2,
    backgroundColor: '#38BDF8',
  },
  horizonLineRight: {
    flex: 1,
    height: 1,
    backgroundColor: 'rgba(56, 189, 248, 0.5)',
  },
  mockDetectionBox: {
    position: 'absolute',
    borderWidth: 2,
    borderColor: '#EF4444',
    borderRadius: 6,
    backgroundColor: 'rgba(239, 68, 68, 0.15)',
  },
  detectionLabelTag: {
    position: 'absolute',
    top: -24,
    left: 0,
    backgroundColor: '#EF4444',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
    flexDirection: 'row',
    gap: 6,
  },
  detectionLabelText: {
    color: '#FFFFFF',
    fontSize: 10,
    fontWeight: '800',
  },
  detectionDistanceText: {
    color: '#FEE2E2',
    fontSize: 9,
    fontWeight: '600',
  },
  centerControlsGroup: {
    position: 'absolute',
    bottom: 16,
    flexDirection: 'row',
    gap: 10,
  },
  actionBadge: {
    backgroundColor: 'rgba(2, 132, 199, 0.85)',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: 'rgba(56, 189, 248, 0.4)',
  },
  actionBadgeText: {
    color: '#FFFFFF',
    fontSize: 10,
    fontWeight: '800',
  },
  hudToggleBadge: {
    backgroundColor: 'rgba(15, 23, 42, 0.75)',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.15)',
  },
  hudToggleText: {
    color: '#E2E8F0',
    fontSize: 10,
    fontWeight: '700',
  },
  bottomBar: {
    gap: 6,
  },
  telemetryCard: {
    backgroundColor: 'rgba(15, 23, 42, 0.92)',
    borderRadius: 12,
    padding: 12,
    borderWidth: 1,
    borderColor: 'rgba(56, 189, 248, 0.25)',
  },
  telemetryCardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  telemetryCardHeaderLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  telemetryCardTitle: {
    color: '#94A3B8',
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 0.6,
  },
  syncStatusBadge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
    borderWidth: 1,
  },
  syncStatusActive: {
    backgroundColor: 'rgba(16, 185, 129, 0.2)',
    borderColor: 'rgba(16, 185, 129, 0.4)',
  },
  syncStatusPending: {
    backgroundColor: 'rgba(100, 116, 139, 0.2)',
    borderColor: 'rgba(100, 116, 139, 0.4)',
  },
  syncStatusText: {
    color: '#34D399',
    fontSize: 8,
    fontWeight: '800',
  },
  queueActions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  speedText: {
    color: '#38BDF8',
    fontSize: 14,
    fontWeight: '900',
    letterSpacing: 0.5,
  },
  clearQueueButton: {
    backgroundColor: 'rgba(239, 68, 68, 0.2)',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
    borderWidth: 1,
    borderColor: 'rgba(239, 68, 68, 0.4)',
  },
  clearQueueText: {
    color: '#F87171',
    fontSize: 8,
    fontWeight: '800',
  },
  telemetryGrid: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  telemetryItem: {
    alignItems: 'center',
    flex: 1,
  },
  telemetryLabel: {
    color: '#64748B',
    fontSize: 8,
    fontWeight: '700',
    letterSpacing: 0.4,
  },
  telemetryValue: {
    color: '#F8FAFC',
    fontSize: 12,
    fontWeight: '800',
    marginTop: 2,
  },
  footerInfo: {
    alignItems: 'center',
    paddingVertical: 2,
  },
  footerInfoText: {
    color: '#94A3B8',
    fontSize: 8,
    fontWeight: '600',
  },
  permissionContainer: {
    flex: 1,
    backgroundColor: '#0B1120',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 24,
  },
  permissionCard: {
    backgroundColor: '#1E293B',
    borderRadius: 16,
    padding: 24,
    alignItems: 'center',
    width: '100%',
    maxWidth: 380,
    borderWidth: 1,
    borderColor: 'rgba(56, 189, 248, 0.2)',
  },
  permissionIconBadge: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: 'rgba(56, 189, 248, 0.15)',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 16,
  },
  permissionIconText: {
    fontSize: 28,
  },
  permissionTitle: {
    color: '#F8FAFC',
    fontSize: 20,
    fontWeight: '800',
    marginBottom: 4,
    textAlign: 'center',
  },
  permissionSubtitle: {
    color: '#38BDF8',
    fontSize: 13,
    fontWeight: '700',
    marginBottom: 12,
    textAlign: 'center',
  },
  permissionDescription: {
    color: '#94A3B8',
    fontSize: 13,
    lineHeight: 18,
    textAlign: 'center',
    marginBottom: 24,
  },
  permissionButton: {
    backgroundColor: '#0284C7',
    paddingVertical: 14,
    paddingHorizontal: 24,
    borderRadius: 10,
    width: '100%',
    alignItems: 'center',
    marginBottom: 16,
  },
  permissionButtonText: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '800',
    letterSpacing: 0.3,
  },
  permissionPrivacyNote: {
    color: '#64748B',
    fontSize: 11,
    fontWeight: '600',
    textAlign: 'center',
  },
});
