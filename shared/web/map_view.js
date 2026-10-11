// Plain lat/lon map (no tiles, works offline), north-up.
// drawMap(canvas, { trail: [[lat, lon], ...], stale: bool, events: [{lat, lon, severity}] })
// Autoscales to the trail and event markers. The pin is the last trail point.
const MAP_SEVERITY = { low: '#3fb950', medium: '#d29922', high: '#f85149' };

function drawMap(canvas, m) {
  const r = devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * r) || canvas.height !== Math.round(h * r)) {
    canvas.width = Math.round(w * r); canvas.height = Math.round(h * r);
  }
  const ctx = canvas.getContext('2d'); ctx.setTransform(r, 0, 0, r, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const trail = m.trail || [], events = (m.events || []).filter(e => e.lat != null);
  const pts = trail.concat(events.map(e => [e.lat, e.lon]));
  ctx.font = '13px system-ui'; ctx.fillStyle = '#8b98a5';
  if (!pts.length) { ctx.fillText('waiting for position...', 16, 24); return; }

  let [minLa, maxLa, minLo, maxLo] = [Infinity, -Infinity, Infinity, -Infinity];
  for (const [la, lo] of pts) { minLa = Math.min(minLa, la); maxLa = Math.max(maxLa, la); minLo = Math.min(minLo, lo); maxLo = Math.max(maxLo, lo); }
  const midLa = (minLa + maxLa) / 2, kx = Math.cos(midLa * Math.PI / 180);
  const spanX = Math.max((maxLo - minLo) * kx, 2e-5), spanY = Math.max(maxLa - minLa, 2e-5);
  const pad = 36, s = Math.min((w - 2 * pad) / spanX, (h - 2 * pad) / spanY);
  const ox = (w - spanX * s) / 2, oy = (h - spanY * s) / 2;
  const xy = ([la, lo]) => [ox + (lo - minLo) * kx * s, oy + (maxLa - la) * s];

  ctx.strokeStyle = '#58a6ff'; ctx.lineWidth = 3; ctx.lineJoin = 'round'; ctx.beginPath();
  trail.forEach((p, i) => { const [x, y] = xy(p); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
  ctx.stroke();

  for (const e of events) {
    const [x, y] = xy([e.lat, e.lon]);
    ctx.fillStyle = MAP_SEVERITY[e.severity] || '#8b98a5';
    ctx.beginPath(); ctx.arc(x, y, 7, 0, 2 * Math.PI); ctx.fill();
    ctx.strokeStyle = '#0f1419'; ctx.lineWidth = 2; ctx.stroke();
  }

  if (trail.length) {
    const [x, y] = xy(trail[trail.length - 1]);
    ctx.fillStyle = m.stale ? '#f85149' : '#58a6ff';
    ctx.beginPath(); ctx.arc(x, y, 10, 0, 2 * Math.PI); ctx.fill();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke();
  }

  // scale bar (metres) and north arrow
  const mPerPx = 111320 / s, target = 60 * mPerPx, nice = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000]
    .find(v => v >= target) || 5000, len = nice / mPerPx;
  ctx.strokeStyle = ctx.fillStyle = '#8b98a5'; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(12, h - 14); ctx.lineTo(12 + len, h - 14); ctx.stroke();
  ctx.fillText(nice >= 1000 ? nice / 1000 + ' km' : nice + ' m', 12, h - 20);
  ctx.fillText('N ↑', w - 36, 20);
}
