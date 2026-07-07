// Sun-elevation contours (0 / -6 / -12 / -18 deg), computed geometrically --
// no rastering. Each contour is a small circle of fixed angular radius
// (90 + depression) around the subsolar point; sample points around it,
// project through the same Lambert Conformal projection as the data grid
// (mepscloud/config.py's NATIVE_PROJ4 -- kept in sync manually, see there),
// draw as an SVG path. Recomputed per displayed frame since the subsolar
// point moves.
//
// Solar position formula ported from the (deferred) meteogram prototype's
// solar.py -- standard low-precision NOAA-style algorithm, ~0.01 deg.

const DEG = Math.PI / 180, RAD = 180 / Math.PI;

function daysSinceJ2000(date) {
  return date.getTime() / 86400000 + 2440587.5 - 2451545.0;
}

// Subsolar point (lat, lon in degrees) at a given UTC Date.
function subsolarPoint(date) {
  const n = daysSinceJ2000(date);
  const L = DEG * (((280.460 + 0.9856474 * n) % 360) + 360) % 360;
  const g = DEG * (((357.528 + 0.9856003 * n) % 360) + 360) % 360;
  const lam = L + DEG * 1.915 * Math.sin(g) + DEG * 0.020 * Math.sin(2 * g);
  const eps = DEG * (23.439 - 4.0e-7 * n);
  const dec = Math.asin(Math.sin(eps) * Math.sin(lam));
  const ra = Math.atan2(Math.cos(eps) * Math.sin(lam), Math.cos(lam));
  const gmst = (((280.46061837 + 360.98564736629 * n) % 360) + 360) % 360;
  let lon = ra * RAD - gmst;
  lon = ((lon + 180) % 360 + 360) % 360 - 180;
  return { lat: dec * RAD, lon };
}

// Point at angular distance `radiusDeg` and `bearingRad` from (lat0,lon0),
// all on the sphere -- standard destination-point-given-distance formula.
function destPoint(lat0Deg, lon0Deg, radiusDeg, bearingRad) {
  const lat0 = lat0Deg * DEG, lon0 = lon0Deg * DEG, r = radiusDeg * DEG;
  const lat = Math.asin(Math.sin(lat0) * Math.cos(r) + Math.cos(lat0) * Math.sin(r) * Math.cos(bearingRad));
  const lon = lon0 + Math.atan2(
    Math.sin(bearingRad) * Math.sin(r) * Math.cos(lat0),
    Math.cos(r) - Math.sin(lat0) * Math.sin(lat)
  );
  return { lat: lat * RAD, lon: lon * RAD };
}

// Lambert Conformal Conic, spherical tangent case (lat_1=lat_2=lat_0) --
// must match mepscloud/config.py's NATIVE_PROJ4 exactly:
//   "+proj=lcc +lat_1=63.3 +lat_2=63.3 +lat_0=63.3 +lon_0=15 +R=6371000"
const PROJ = (() => {
  const R = 6371000, lat0 = 63.3 * DEG, lon0 = 15 * DEG;
  const n = Math.sin(lat0);
  const F = Math.cos(lat0) * Math.pow(Math.tan(Math.PI / 4 + lat0 / 2), n) / n;
  const rho0 = R * F / Math.pow(Math.tan(Math.PI / 4 + lat0 / 2), n);
  return {
    forward(latDeg, lonDeg) {
      const lat = latDeg * DEG, lon = lonDeg * DEG;
      const rho = R * F / Math.pow(Math.tan(Math.PI / 4 + lat / 2), n);
      const theta = n * (lon - lon0);
      return [rho * Math.sin(theta), rho0 - rho * Math.cos(theta)];
    },
  };
})();

// Native x/y (metres) -> pixel coords matching the frame images (row 0 =
// north, since frames are flipud'd at render time -- see render.py).
function toPixel(x, y, grid) {
  const px = (x - grid.x_min) / (grid.x_max - grid.x_min) * grid.nx;
  const py = (grid.y_max - y) / (grid.y_max - grid.y_min) * grid.ny;
  return [px, py];
}

// One contour's pixel-space point list for a given UTC Date + depression
// angle (degrees below horizon: 0, 6, 12, 18). Points that fail to project
// finitely are dropped rather than crashing the whole contour.
function twilightContourPixels(date, depressionDeg, grid, nSamples = 180) {
  const sub = subsolarPoint(date);
  const radius = 90 + depressionDeg;
  const pts = [];
  for (let i = 0; i <= nSamples; i++) {
    const bearing = (i / nSamples) * 2 * Math.PI;
    const { lat, lon } = destPoint(sub.lat, sub.lon, radius, bearing);
    const [x, y] = PROJ.forward(lat, lon);
    const [px, py] = toPixel(x, y, grid);
    if (Number.isFinite(px) && Number.isFinite(py)) pts.push([px, py]);
  }
  return pts;
}
