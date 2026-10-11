// Shared IMU panel for the Dashscreen and the Dashboard.
// drawImu(canvas, imuSnapshot, readoutEl): strip chart of vertical deviation |az - 1| (g)
// over the snapshot window, plus a numeric readout of the newest sample.
function drawImu(canvas, imu, readout) {
  const r = devicePixelRatio || 1;
  const cw = Math.round(canvas.clientWidth * r), ch = Math.round(canvas.clientHeight * r);
  if (canvas.width !== cw || canvas.height !== ch) { canvas.width = cw; canvas.height = ch; }
  const ctx = canvas.getContext('2d'); ctx.setTransform(r, 0, 0, r, 0, 0);
  const w = canvas.clientWidth, h = canvas.clientHeight;
  ctx.clearRect(0, 0, w, h);
  const s = imu.samples || [];
  if (readout) {
    const l = imu.latest;
    readout.textContent = !l ? (imu.error ? 'IMU offline' : 'waiting for IMU...') :
      `ax ${l.ax.toFixed(2)}  ay ${l.ay.toFixed(2)}  az ${l.az.toFixed(2)} g   |   gx ${l.gx.toFixed(0)}  gy ${l.gy.toFixed(0)}  gz ${l.gz.toFixed(0)} °/s` + (imu.stale ? '   STALE' : '');
  }
  if (s.length < 2) return;
  const t = s.map(x => Date.parse(x.ts)), t1 = t[t.length - 1], t0 = t1 - 5000;
  const dev = s.map(x => Math.abs(x.az - 1));
  const top = Math.max(0.5, ...dev) * 1.1;
  const X = ms => (ms - t0) / 5000 * w, Y = d => h - 4 - d / top * (h - 8);
  ctx.strokeStyle = '#30363d'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(0, Y(0)); ctx.lineTo(w, Y(0)); ctx.stroke();
  ctx.strokeStyle = imu.stale ? '#f85149' : '#d29922'; ctx.lineWidth = 2; ctx.beginPath();
  s.forEach((_, i) => i ? ctx.lineTo(X(t[i]), Y(dev[i])) : ctx.moveTo(X(t[i]), Y(dev[i])));
  ctx.stroke();
  ctx.fillStyle = '#8b98a5'; ctx.font = '12px system-ui'; ctx.fillText(`|az-1| g, peak ${Math.max(...dev).toFixed(2)}`, 6, 14);
}
