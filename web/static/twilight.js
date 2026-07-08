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
      const lat = latDeg * DEG;
      // destPoint()'s returned longitude is subsolar_lon + atan2(...), which
      // is NOT normalized -- it can land well outside +-180 as bearing
      // sweeps through a full circle. Wrapping (lon - lon0) here, rather
      // than trusting the input range, avoids a fake ~n*360deg jump in
      // theta (and thus in projected x/y) whenever that raw value crosses
      // +-180 -- which isn't a real projection discontinuity, just unwrapped
      // input, but produces an indistinguishable-looking huge jump that
      // upstream discontinuity detection (twilightContourSegments) would
      // otherwise "correctly" but wrongly break the path on.
      let dLonDeg = (lonDeg - lon0 * RAD) % 360;
      if (dLonDeg > 180) dLonDeg -= 360;
      else if (dLonDeg < -180) dLonDeg += 360;
      const lon = dLonDeg * DEG;
      const rho = R * F / Math.pow(Math.tan(Math.PI / 4 + lat / 2), n);
      const theta = n * lon;
      return [rho * Math.sin(theta), rho0 - rho * Math.cos(theta)];
    },
    // Inverse of forward: native x/y (metres) -> [latDeg, lonDeg]. Used by the
    // meteogram to show the dragged marker's coordinates (F cancels, so the
    // odd /n baked into F above doesn't matter here). n>0 since lat0>0.
    inverse(x, y) {
      const dy = rho0 - y;
      const rho = Math.sqrt(x * x + dy * dy);
      const theta = Math.atan2(x, dy);            // x = rho·sinθ, dy = rho·cosθ
      const lonDeg = 15 + (theta / n) * RAD;       // lon0 = 15°E
      const latDeg = (2 * Math.atan(Math.pow(R * F / rho, 1 / n)) - Math.PI / 2) * RAD;
      return [latDeg, lonDeg];
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

// Inverse of toPixel: pixel (col,row) -> native x/y (metres).
function fromPixel(px, py, grid) {
  const x = grid.x_min + px / grid.nx * (grid.x_max - grid.x_min);
  const y = grid.y_max - py / grid.ny * (grid.y_max - grid.y_min);
  return [x, y];
}

// Convenience round-trips used by the meteogram marker.
function latLonToPixel(latDeg, lonDeg, grid) {
  const [x, y] = PROJ.forward(latDeg, lonDeg);
  return toPixel(x, y, grid);
}
function pixelToLatLon(px, py, grid) {
  const [x, y] = fromPixel(px, py, grid);
  return PROJ.inverse(x, y);   // [latDeg, lonDeg]
}

// One contour's pixel-space point segments for a given UTC Date + depression
// angle (degrees below horizon: 0, 6, 12, 18). Returns an array of point
// arrays ("segments"), NOT one flat list: the sampled circle can pass near
// the Lambert projection's singularity (opposite side of the globe from its
// tangent point, reachable since a -18 deg contour has a 108 deg angular
// radius), where rho blows up toward infinity. A naive single polyline
// would connect a sane point straight to that near-infinite one -- a stray
// line shooting across the whole visible frame, clipped only by the SVG
// viewport edge (looks like the contour "runs off the side"). Instead we
// break into a new segment wherever consecutive points jump implausibly
// far, so only the genuinely continuous, in-view parts of the curve draw.
function twilightContourSegments(date, depressionDeg, grid, nSamples = 180) {
  const sub = subsolarPoint(date);
  const radius = 90 + depressionDeg;
  const maxJumpPx = 2 * Math.max(grid.nx, grid.ny); // discontinuity threshold
  const segments = [];
  let current = [];
  let prev = null;
  for (let i = 0; i <= nSamples; i++) {
    const bearing = (i / nSamples) * 2 * Math.PI;
    const { lat, lon } = destPoint(sub.lat, sub.lon, radius, bearing);
    const [x, y] = PROJ.forward(lat, lon);
    const [px, py] = toPixel(x, y, grid);
    const valid = Number.isFinite(px) && Number.isFinite(py);
    const jumped = valid && prev && Math.hypot(px - prev[0], py - prev[1]) > maxJumpPx;
    if (!valid || jumped) {
      if (current.length > 1) segments.push(current);
      current = [];
      prev = null;
    }
    if (valid) {
      current.push([px, py]);
      prev = [px, py];
    }
  }
  if (current.length > 1) segments.push(current);
  return segments;
}

// Full (unbroken, clamped) loop for FILLING rather than stroking: the
// "beyond this depression" disk boundary, as one closed point list. Used
// with fill-rule=evenodd against the viewport rectangle (see index.html)
// so the browser's own rasterizer computes "inside viewport but outside
// this disk" = the dark region, without hand-rolled polygon clipping.
// Points near the Lambert projection's high-distortion region (see
// twilightContourSegments) are clamped to a large-but-finite value rather
// than broken into segments -- evenodd only cares about crossings near the
// (tiny, by comparison) viewport, so a coarse clamp far outside it doesn't
// affect correctness there, and a clamped-but-closed loop is what evenodd
// fill needs.
function twilightFillLoop(date, depressionDeg, grid, nSamples = 180) {
  const sub = subsolarPoint(date);
  const radius = 90 + depressionDeg;
  const CLAMP = 1e6;
  const pts = [];
  for (let i = 0; i < nSamples; i++) {
    const bearing = (i / nSamples) * 2 * Math.PI;
    const { lat, lon } = destPoint(sub.lat, sub.lon, radius, bearing);
    const [x, y] = PROJ.forward(lat, lon);
    let [px, py] = toPixel(x, y, grid);
    if (!Number.isFinite(px)) px = 0;
    if (!Number.isFinite(py)) py = 0;
    px = Math.max(-CLAMP, Math.min(CLAMP, px));
    py = Math.max(-CLAMP, Math.min(CLAMP, py));
    pts.push([px, py]);
  }
  return pts;
}
