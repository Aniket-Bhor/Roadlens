import { useState, useEffect, useRef, useCallback } from 'react';
import * as Location from 'expo-location';
import { Magnetometer } from 'expo-sensors';
import { useSharedValue } from 'react-native-worklets-core';
import { VehicleTelemetry, SynchronizedTelemetrySnapshot } from '../types/camera';
import { calculateCompassHeading, getCardinalDirection, speedMpsToKmh } from '../utils/compass';

const GPS_STALENESS_THRESHOLD_MS = 3000; // Retain last-known coordinate for up to 3 seconds (tunnel traversal)

const INITIAL_SNAPSHOT: SynchronizedTelemetrySnapshot = {
  latitude: null,
  longitude: null,
  altitude: null,
  speedKmh: null,
  headingDeg: null,
  cardinal: '--',
  isGpsStale: false,
  gpsLock: false,
  timestampMs: 0,
};

export function useVehicleTelemetry() {
  const [telemetry, setTelemetry] = useState<VehicleTelemetry>({
    latitude: null,
    longitude: null,
    altitude: null,
    speedKmh: null,
    headingDeg: null,
    cardinal: '--',
    hasLocationPermission: false,
    gpsLock: false,
    isGpsStale: false,
    lastGpsTimestamp: 0,
    lastMagTimestamp: 0,
  });

  // Thread-safe SharedValue accessible synchronously by the Worklet frame processor
  const telemetryShared = useSharedValue<SynchronizedTelemetrySnapshot>(INITIAL_SNAPSHOT);

  // References to preserve last known data without triggering re-render cycles
  const cachedGpsRef = useRef<{
    latitude: number | null;
    longitude: number | null;
    altitude: number | null;
    speedKmh: number | null;
    timestamp: number;
  }>({
    latitude: null,
    longitude: null,
    altitude: null,
    speedKmh: null,
    timestamp: 0,
  });

  const cachedHeadingRef = useRef<{
    headingDeg: number | null;
    cardinal: string;
    timestamp: number;
  }>({
    headingDeg: null,
    cardinal: '--',
    timestamp: 0,
  });

  // Synchronizes current cached telemetry to both React state and the Worklet SharedValue
  const syncTelemetryToShared = useCallback((now: number) => {
    const gpsAge = now - cachedGpsRef.current.timestamp;
    const isGpsWithinWindow = cachedGpsRef.current.timestamp > 0 && gpsAge <= GPS_STALENESS_THRESHOLD_MS;
    const isGpsStale = isGpsWithinWindow && gpsAge > 1200; // Stale if no ping in >1.2s but still within 3s tunnel retention

    const activeLat = isGpsWithinWindow ? cachedGpsRef.current.latitude : null;
    const activeLon = isGpsWithinWindow ? cachedGpsRef.current.longitude : null;
    const activeAlt = isGpsWithinWindow ? cachedGpsRef.current.altitude : null;
    const activeSpeed = isGpsWithinWindow ? cachedGpsRef.current.speedKmh : null;
    const hasGpsLock = isGpsWithinWindow;

    const snapshot: SynchronizedTelemetrySnapshot = {
      latitude: activeLat,
      longitude: activeLon,
      altitude: activeAlt,
      speedKmh: activeSpeed,
      headingDeg: cachedHeadingRef.current.headingDeg,
      cardinal: cachedHeadingRef.current.cardinal,
      isGpsStale,
      gpsLock: hasGpsLock,
      timestampMs: now,
    };

    // Update Worklet shared value (lock-free, zero-copy for frame processor)
    telemetryShared.value = snapshot;

    // Update React state for UI rendering
    setTelemetry((prev) => ({
      ...prev,
      latitude: activeLat,
      longitude: activeLon,
      altitude: activeAlt,
      speedKmh: activeSpeed,
      headingDeg: cachedHeadingRef.current.headingDeg,
      cardinal: cachedHeadingRef.current.cardinal,
      gpsLock: hasGpsLock,
      isGpsStale,
      lastGpsTimestamp: cachedGpsRef.current.timestamp,
      lastMagTimestamp: cachedHeadingRef.current.timestamp,
    }));
  }, [telemetryShared]);

  // 1. Location Services Lifecycle (expo-location)
  useEffect(() => {
    let isMounted = true;
    let locationSubscription: Location.LocationSubscription | null = null;

    async function initLocation() {
      try {
        const { status } = await Location.requestForegroundPermissionsAsync();
        if (!isMounted) return;

        if (status !== 'granted') {
          setTelemetry((prev) => ({ ...prev, hasLocationPermission: false }));
          return;
        }

        setTelemetry((prev) => ({ ...prev, hasLocationPermission: true }));

        // Attempt initial last known fix to immediately populate coordinates
        try {
          const lastFix = await Location.getLastKnownPositionAsync();
          if (isMounted && lastFix) {
            const now = Date.now();
            cachedGpsRef.current = {
              latitude: lastFix.coords.latitude,
              longitude: lastFix.coords.longitude,
              altitude: lastFix.coords.altitude,
              speedKmh: speedMpsToKmh(lastFix.coords.speed),
              timestamp: now,
            };
            syncTelemetryToShared(now);
          }
        } catch {
          // Last known fix not cached yet; continue to live stream
        }

        // High accuracy foreground location listener
        locationSubscription = await Location.watchPositionAsync(
          {
            accuracy: Location.Accuracy.BestForNavigation,
            timeInterval: 200,
            distanceInterval: 0.2,
          },
          (location) => {
            if (!isMounted) return;
            const now = Date.now();
            cachedGpsRef.current = {
              latitude: location.coords.latitude,
              longitude: location.coords.longitude,
              altitude: location.coords.altitude,
              speedKmh: speedMpsToKmh(location.coords.speed),
              timestamp: now,
            };
            syncTelemetryToShared(now);
          }
        );
      } catch (err) {
        console.warn('Error initializing Location service:', err);
      }
    }

    initLocation();

    return () => {
      isMounted = false;
      if (locationSubscription) {
        locationSubscription.remove();
      }
    };
  }, [syncTelemetryToShared]);

  // 2. Orientation Services Lifecycle (expo-sensors Magnetometer)
  useEffect(() => {
    let isMounted = true;
    let magSubscription: { remove: () => void } | null = null;

    async function initMagnetometer() {
      try {
        const available = await Magnetometer.isAvailableAsync();
        if (!isMounted || !available) return;

        // Set magnetometer update rate matching frame loop (~200ms)
        Magnetometer.setUpdateInterval(200);

        magSubscription = Magnetometer.addListener((measurement) => {
          if (!isMounted) return;
          const now = Date.now();
          const headingDeg = calculateCompassHeading(measurement.x, measurement.y);
          const cardinal = getCardinalDirection(headingDeg);

          cachedHeadingRef.current = {
            headingDeg,
            cardinal,
            timestamp: now,
          };

          syncTelemetryToShared(now);
        });
      } catch (err) {
        console.warn('Error initializing Magnetometer sensor:', err);
      }
    }

    initMagnetometer();

    return () => {
      isMounted = false;
      if (magSubscription) {
        magSubscription.remove();
      }
    };
  }, [syncTelemetryToShared]);

  // Staleness watchdog timer: runs every 1 second to update tunnel/stale state if no GPS ping arrives
  useEffect(() => {
    const watchdogInterval = setInterval(() => {
      syncTelemetryToShared(Date.now());
    }, 1000);

    return () => clearInterval(watchdogInterval);
  }, [syncTelemetryToShared]);

  return {
    telemetry,
    telemetryShared,
  };
}
