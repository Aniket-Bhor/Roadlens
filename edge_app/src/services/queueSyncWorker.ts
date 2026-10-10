import { peekQueue, dequeueHazardPayloads, getQueueLength } from './payloadQueue';
import { ENV } from '../config/env';

let isSyncing = false;

/**
 * Periodically flushes pending offline edge hazard detections to the FastAPI backend.
 * Implements store-and-forward architecture with automatic network resilience.
 */
export async function flushQueueToBackend(): Promise<{ sent: number; remaining: number }> {
  if (isSyncing) {
    return { sent: 0, remaining: await getQueueLength() };
  }

  isSyncing = true;

  try {
    const items = await peekQueue(20);
    if (items.length === 0) {
      isSyncing = false;
      return { sent: 0, remaining: 0 };
    }

    let sentCount = 0;
    for (const item of items) {
      try {
        const response = await fetch(`${ENV.apiBaseUrl}/api/v1/telemetry`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(item),
        });

        if (response.ok) {
          sentCount++;
        } else {
          // If server returns HTTP error, halt batch to prevent data loss
          break;
        }
      } catch {
        // Network offline / timeout, break and retry on next tick
        break;
      }
    }

    if (sentCount > 0) {
      await dequeueHazardPayloads(sentCount);
    }

    const remaining = await getQueueLength();
    isSyncing = false;
    return { sent: sentCount, remaining };
  } catch {
    isSyncing = false;
    return { sent: 0, remaining: await getQueueLength() };
  }
}
