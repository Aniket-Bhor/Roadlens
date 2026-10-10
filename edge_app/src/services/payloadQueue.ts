import AsyncStorage from '@react-native-async-storage/async-storage';
import { RoadHazardPayload } from '../types/payload';
import { ENV } from '../config/env';

const QUEUE_STORAGE_KEY = '@roadlens_hazard_queue_v1';
const DEVICE_ID_KEY = '@roadlens_device_id_v1';

let cachedDeviceId: string | null = null;

/**
 * Returns a persistent, unique device identifier for this Scout edge client.
 */
export async function getOrCreateDeviceId(): Promise<string> {
  if (cachedDeviceId) return cachedDeviceId;

  try {
    const existing = await AsyncStorage.getItem(DEVICE_ID_KEY);
    if (existing) {
      cachedDeviceId = existing;
      return existing;
    }

    const generated = `scout-edge-${Math.random().toString(36).substring(2, 10)}`;
    await AsyncStorage.setItem(DEVICE_ID_KEY, generated);
    cachedDeviceId = generated;
    return generated;
  } catch {
    return 'scout-edge-fallback';
  }
}

/**
 * Persists an extracted hazard detection payload to the offline store-and-forward queue.
 * Strictly respects ENV.offlineQueueMaxSize to prevent mobile storage exhaustion.
 *
 * @param payload The RoadHazardPayload to enqueue
 * @returns The new count of items in the offline queue
 */
export async function enqueueHazardPayload(payload: RoadHazardPayload): Promise<number> {
  try {
    const raw = await AsyncStorage.getItem(QUEUE_STORAGE_KEY);
    const queue: RoadHazardPayload[] = raw ? JSON.parse(raw) : [];

    queue.push(payload);

    // Enforce FIFO ceiling if queue exceeds maximum limit
    if (queue.length > ENV.offlineQueueMaxSize) {
      queue.splice(0, queue.length - ENV.offlineQueueMaxSize);
    }

    await AsyncStorage.setItem(QUEUE_STORAGE_KEY, JSON.stringify(queue));
    return queue.length;
  } catch (error) {
    console.warn('[PayloadQueue] Failed to enqueue payload:', error);
    return 0;
  }
}

/**
 * Retrieves the current number of queued offline payloads.
 */
export async function getQueueLength(): Promise<number> {
  try {
    const raw = await AsyncStorage.getItem(QUEUE_STORAGE_KEY);
    if (!raw) return 0;
    const queue: RoadHazardPayload[] = JSON.parse(raw);
    return queue.length;
  } catch {
    return 0;
  }
}

/**
 * Peeks up to `limit` payloads from the queue without removing them.
 */
export async function peekQueue(limit = 10): Promise<RoadHazardPayload[]> {
  try {
    const raw = await AsyncStorage.getItem(QUEUE_STORAGE_KEY);
    if (!raw) return [];
    const queue: RoadHazardPayload[] = JSON.parse(raw);
    return queue.slice(0, limit);
  } catch {
    return [];
  }
}

/**
 * Removes up to `count` items from the head of the offline payload queue (FIFO).
 */
export async function dequeueHazardPayloads(count = 1): Promise<RoadHazardPayload[]> {
  try {
    const raw = await AsyncStorage.getItem(QUEUE_STORAGE_KEY);
    if (!raw) return [];
    const queue: RoadHazardPayload[] = JSON.parse(raw);
    const removed = queue.splice(0, count);
    await AsyncStorage.setItem(QUEUE_STORAGE_KEY, JSON.stringify(queue));
    return removed;
  } catch (error) {
    console.warn('[PayloadQueue] Failed to dequeue payloads:', error);
    return [];
  }
}

/**
 * Clears the offline payload queue (e.g. after successful upstream dispatch).
 */
export async function clearQueue(): Promise<void> {
  try {
    await AsyncStorage.removeItem(QUEUE_STORAGE_KEY);
  } catch (error) {
    console.warn('[PayloadQueue] Failed to clear queue:', error);
  }
}
