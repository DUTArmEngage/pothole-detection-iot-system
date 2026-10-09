"""
app.py — Road-condition dashboard.

Serves the two views described in Section 3.6.1 from one database: a municipal
operations view oriented to maintenance prioritisation, and a road-user view
oriented to hazard awareness. The filtering difference between them is the
severity policy: low-severity detections appear in the municipal view for
tracking but are withheld from the road-user view to avoid alert fatigue.

Standard library only — no framework dependency, so it runs on a bare Pi.

    python dashboard/app.py --db data/events.db --port 8000
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "edge"))
from buffer import EventBuffer, cluster_events


DB_PATH = "data/events.db"

PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Road Condition Monitor</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
background:#f4f5f7;color:#16191d}
header{background:#1a3a5c;color:#fff;padding:14px 22px;display:flex;
align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}
header h1{font-size:17px;font-weight:600;letter-spacing:.2px}
header .sub{font-size:12px;opacity:.75;margin-top:2px}
.views{display:flex;gap:6px}
.views button{background:rgba(255,255,255,.12);color:#fff;border:1px solid rgba(255,255,255,.25);
padding:7px 15px;border-radius:4px;cursor:pointer;font-size:13px}
.views button.on{background:#fff;color:#1a3a5c;font-weight:600}
.wrap{display:grid;grid-template-columns:1fr 340px;gap:14px;padding:14px}
@media(max-width:900px){.wrap{grid-template-columns:1fr}}
.card{background:#fff;border:1px solid #dfe3e8;border-radius:6px;overflow:hidden}
.card h2{font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:#5b6570;
padding:11px 14px;border-bottom:1px solid #e8ebee;font-weight:600}
#map{height:560px}
.kpis{display:grid;grid-template-columns:repeat(2,1fr);gap:1px;background:#e8ebee}
.kpi{background:#fff;padding:14px}
.kpi .v{font-size:23px;font-weight:600;color:#1a3a5c}
.kpi .l{font-size:11px;color:#6b7580;text-transform:uppercase;letter-spacing:.4px;margin-top:2px}
.feed{max-height:340px;overflow-y:auto}
.ev{padding:10px 14px;border-bottom:1px solid #f0f2f4;display:flex;
justify-content:space-between;align-items:flex-start;gap:10px}
.ev:last-child{border-bottom:0}
.ev .m{font-size:12px}
.ev .c{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:#6b7580}
.pill{font-size:10px;padding:2px 8px;border-radius:10px;font-weight:600;
text-transform:uppercase;letter-spacing:.4px;white-space:nowrap}
.high{background:#fdecec;color:#a11d1d}
.medium{background:#fdf3e3;color:#8a5a00}
.low{background:#eef1f4;color:#5b6570}
.legend{padding:11px 14px;font-size:12px;color:#5b6570;border-top:1px solid #e8ebee}
.legend span{display:inline-flex;align-items:center;gap:5px;margin-right:14px}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block}
.note{padding:11px 14px;font-size:12px;color:#6b7580;background:#fafbfc;
border-top:1px solid #e8ebee}
</style></head><body>

<header>
  <div><h1>Road Condition Monitor</h1>
  <div class="sub">IoT edge detection framework &middot; KwaZulu-Natal</div></div>
  <div class="views">
    <button id="bM" class="on" onclick="setView('municipal')">Municipal view</button>
    <button id="bD" onclick="setView('driver')">Road-user view</button>
  </div>
</header>

<div class="wrap">
  <div class="card">
    <h2 id="mapTitle">Detected road defects</h2>
    <div id="map"></div>
    <div class="legend">
      <span><i class="dot" style="background:#a11d1d"></i> High</span>
      <span><i class="dot" style="background:#d68910"></i> Medium</span>
      <span id="lowLeg"><i class="dot" style="background:#8a93a0"></i> Low</span>
      <span style="float:right" id="clusterNote"></span>
    </div>
  </div>

  <div style="display:flex;flex-direction:column;gap:14px">
    <div class="card">
      <h2>Summary</h2>
      <div class="kpis" id="kpis"></div>
      <div class="note" id="viewNote"></div>
    </div>
    <div class="card">
      <h2>Detection feed</h2>
      <div class="feed" id="feed"></div>
    </div>
  </div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
let view='municipal', map, layer, clustered=true;
const COL={high:'#a11d1d',medium:'#d68910',low:'#8a93a0'};

function initMap(){
  map=L.map('map').setView([-29.8587,31.0218],13);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    {maxZoom:19,attribution:'&copy; OpenStreetMap'}).addTo(map);
  layer=L.layerGroup().addTo(map);
}

function setView(v){
  view=v;
  document.getElementById('bM').className = v==='municipal'?'on':'';
  document.getElementById('bD').className = v==='driver'?'on':'';
  document.getElementById('mapTitle').textContent =
    v==='municipal' ? 'Detected road defects — all severities'
                    : 'Road hazards ahead — high and medium severity';
  document.getElementById('lowLeg').style.display = v==='municipal'?'inline-flex':'none';
  document.getElementById('viewNote').textContent = v==='municipal'
    ? 'All detections shown, including low severity, for deterioration tracking and maintenance prioritisation.'
    : 'Low-severity detections withheld to preserve signal-to-noise for road users making real-time decisions.';
  load();
}

async function load(){
  const r=await fetch('/api/events?view='+view);
  const d=await r.json();
  layer.clearLayers();

  d.clusters.forEach(c=>{
    const n=c.observation_count;
    L.circleMarker([c.latitude,c.longitude],{
      radius: Math.min(6+n*1.6,16), color:'#fff', weight:1.5,
      fillColor:COL[c.severity], fillOpacity:.85
    }).bindPopup(
      `<b>${c.severity.toUpperCase()}</b><br>`+
      `Observations: ${n}<br>`+
      `Peak confidence: ${c.max_confidence.toFixed(2)}<br>`+
      `<code>${c.latitude.toFixed(5)}, ${c.longitude.toFixed(5)}</code>`
    ).addTo(layer);
  });

  if(d.clusters.length){
    map.fitBounds(L.latLngBounds(d.clusters.map(c=>[c.latitude,c.longitude])).pad(.25));
  }

  const s=d.summary;
  document.getElementById('kpis').innerHTML=
    kpi(s.shown,'Shown')+kpi(s.clusters,'Locations')+
    kpi(s.high,'High severity')+kpi(s.medium,'Medium severity');
  document.getElementById('clusterNote').textContent =
    `${s.clusters} locations from ${s.shown} observations`;

  document.getElementById('feed').innerHTML = d.events.slice(-40).reverse().map(e=>
    `<div class="ev"><div class="m">
       <div><b>${e.defect_class}</b> &middot; conf ${e.confidence.toFixed(2)}</div>
       <div class="c">${e.latitude.toFixed(5)}, ${e.longitude.toFixed(5)}</div>
       <div class="c">${e.detected_at.replace('T',' ').slice(0,19)}</div>
     </div><span class="pill ${e.severity}">${e.severity}</span></div>`
  ).join('') || '<div class="ev"><div class="m">No detections recorded.</div></div>';
}

function kpi(v,l){return `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`;}

initMap(); setView('municipal'); setInterval(load,4000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._send(PAGE, "text/html")
        if u.path == "/api/events":
            q = parse_qs(u.query)
            return self._send(json.dumps(self._events(q.get("view", ["municipal"])[0])),
                              "application/json")
        if u.path == "/api/stats":
            return self._send(json.dumps(EventBuffer(DB_PATH).stats()),
                              "application/json")
        self.send_error(404)

    def _events(self, view: str) -> dict:
        buf = EventBuffer(DB_PATH)
        evs = buf.all_events()

        # Severity policy differs by consumer — Section 3.6.1
        if view == "driver":
            shown = [e for e in evs if e["severity"] in ("high", "medium")]
        else:
            shown = evs

        clusters = cluster_events(shown, radius_m=15.0)
        return {
            "events": shown,
            "clusters": clusters,
            "summary": {
                "shown": len(shown),
                "total": len(evs),
                "clusters": len(clusters),
                "high": sum(1 for e in shown if e["severity"] == "high"),
                "medium": sum(1 for e in shown if e["severity"] == "medium"),
                "low": sum(1 for e in shown if e["severity"] == "low"),
            },
        }

    def _send(self, body: str, ctype: str):
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)


def main():
    global DB_PATH
    ap = argparse.ArgumentParser(description="Road-condition dashboard")
    ap.add_argument("--db", default="data/events.db")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args()
    DB_PATH = a.db

    if not Path(a.db).exists():
        print(f"Warning: {a.db} does not exist yet — the map will be empty.")

    print(f"\n  Dashboard: http://localhost:{a.port}")
    print(f"  Database:  {a.db}")
    print("  Ctrl-C to stop\n")
    HTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
