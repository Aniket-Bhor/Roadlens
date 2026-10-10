/**
 * RoadLens Scout - Compass & Telemetry Math Utilities
 */

/**
 * Converts raw magnetometer microtesla readings (x, y) into a 0° - 360° compass heading.
 * In standard smartphone portrait/dashboard orientation:
 * - North is 0° / 360°
 * - East is 90°
 * - South is 180°
 * - West is 270°
 *
 * @param x Magnetic field strength on the X axis in microteslas (μT)
 * @param y Magnetic field strength on the Y axis in microteslas (μT)
 * @returns Heading in degrees between 0 and 360
 */
export function calculateCompassHeading(x: number, y: number): number {
  if (x === 0 && y === 0) {
    return 0;
  }

  // atan2(-x, y) gives the angle relative to magnetic north in portrait orientation
  const angleRad = Math.atan2(-x, y);
  let degrees = angleRad * (180 / Math.PI);

  // Normalize to 0° - 360°
  if (degrees < 0) {
    degrees += 360;
  }

  return Number((degrees % 360).toFixed(1));
}

/**
 * Converts heading degrees into an 8-point cardinal/intercardinal direction.
 *
 * @param headingDeg Heading in degrees (0 - 360)
 * @returns Cardinal direction string ('N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW')
 */
export function getCardinalDirection(headingDeg: number | null): string {
  if (headingDeg == null) return '--';
  const directions = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
  const normalized = (headingDeg % 360 + 360) % 360;
  const index = Math.round(normalized / 45) % 8;
  return directions[index];
}

/**
 * Converts speed from meters/second (m/s) to kilometers/hour (km/h).
 *
 * @param speedMps Speed in m/s
 * @returns Speed in km/h or 0 if negative/null
 */
export function speedMpsToKmh(speedMps: number | null | undefined): number {
  if (speedMps == null || speedMps < 0) return 0;
  return Math.round(speedMps * 3.6);
}
