"""
run_experiment.py — Produces the measurements for Chapter Four.

Executes the experimental design of Section 3.4.1: one replay session is fed to
three configurations, so that any difference in outcome is attributable to
configuration rather than to variation between runs.

  Configuration A   edge inference with multi-sensor fusion   (treatment)
  Configuration B   cloud-only inference, no local buffer     (control 1)
  Configuration C   edge inference, vision only               (control 2)

Paired measurements are tested with a paired t-test at alpha = 0.05, as
committed in the approved research proposal.

Outputs
-------
  results/raw_measurements.json   every recorded value
  results/chapter4_tables.md      tables ready to paste into the dissertation
  results/summary.txt             console-readable digest

Usage
-----
  python bench/run_experiment.py --images data/test_images --model models/pothole.tflite
  python bench/run_experiment.py --video data/route.mp4 --model models/pothole.pt --repeats 5
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "edge"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))

import numpy as np

from pipeline import Pipeline, PipelineConfig, Configuration
from fusion import FusionMode
from buffer import EventBuffer, cluster_events
from replay import ReplaySession, GPXTrack, IMUTrace, DURBAN_ROUTE


# ----------------------------------------------------------------------
def host_profile() -> dict:
    """Record the machine under test, so results are interpretable later."""
    info = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or platform.machine(),
        "python": platform.python_version(),
    }
    try:
        model = Path("/proc/device-tree/model")
        if model.exists():
            info["device_model"] = model.read_text().strip("\x00").strip()
    except Exception:
        pass
    try:
        import psutil
        info["cpu_cores"] = psutil.cpu_count(logical=True)
        info["ram_mb"] = round(psutil.virtual_memory().total / 1024 / 1024)
    except ImportError:
        pass
    return info


def sample_resources() -> dict:
    """Snapshot CPU, memory and temperature. Returns empty dict if unavailable."""
    out: dict = {}
    try:
        import psutil
        out["cpu_percent"] = psutil.cpu_percent(interval=None)
        out["mem_percent"] = psutil.virtual_memory().percent
    except ImportError:
        pass
    for q in ("/sys/class/thermal/thermal_zone0/temp",):
        try:
            out["cpu_temp_c"] = round(int(Path(q).read_text().strip()) / 1000.0, 1)
            break
        except Exception:
            continue
    return out


def paired_t_test(a: List[float], b: List[float]) -> dict:
    """
    Paired t-test without SciPy, so the bench has no heavy dependency.

    Returns t, degrees of freedom, two-tailed p, and Cohen's d for paired data.
    """
    n = min(len(a), len(b))
    if n < 2:
        return {"error": "insufficient paired observations", "n": n}

    d = [a[i] - b[i] for i in range(n)]
    mean_d = statistics.fmean(d)
    sd = statistics.stdev(d) if n > 1 else 0.0
    if sd == 0:
        return {"t": None, "df": n - 1, "p": None, "cohens_d": None,
                "mean_difference": round(mean_d, 6), "n": n,
                "note": "zero variance in differences"}

    t = mean_d / (sd / (n ** 0.5))
    df = n - 1
    p = _t_sf(abs(t), df) * 2
    return {
        "t": round(t, 4),
        "df": df,
        "p": round(p, 6),
        "significant_at_05": bool(p < 0.05),
        "cohens_d": round(mean_d / sd, 4),
        "mean_difference": round(mean_d, 6),
        "n": n,
    }


def _t_sf(t: float, df: int) -> float:
    """Upper-tail probability of Student's t, via the incomplete beta function."""
    x = df / (df + t * t)
    return 0.5 * _betainc(df / 2.0, 0.5, x)


def _betainc(a: float, b: float, x: float) -> float:
    import math
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(math.log(x) * a + math.log(1 - x) * b - lbeta) / a
    f, c, d = 1.0, 1.0, 0.0
    for i in range(200):
        m = i // 2
        if i == 0:
            num = 1.0
        elif i % 2 == 0:
            num = (m * (b - m) * x) / ((a + 2 * m - 1) * (a + 2 * m))
        else:
            num = -((a + m) * (a + b + m) * x) / ((a + 2 * m) * (a + 2 * m + 1))
        d = 1.0 + num * d
        d = 1e-30 if abs(d) < 1e-30 else d
        d = 1.0 / d
        c = 1.0 + num / c
        c = 1e-30 if abs(c) < 1e-30 else c
        f *= c * d
        if abs(1.0 - c * d) < 1e-8:
            break
    return front * (f - 1.0)


# ----------------------------------------------------------------------
def build_session(args) -> ReplaySession:
    """Construct the replay session shared by all configurations."""
    duration_hint = args.max_frames / args.fps if args.max_frames else 60.0

    track = (GPXTrack.from_gpx(args.gpx) if args.gpx
             else GPXTrack.synthetic_route(DURBAN_ROUTE, duration_hint, rate_hz=1.0,
                                           jitter_m=args.gps_jitter))

    if args.imu_trace:
        imu = IMUTrace.load(args.imu_trace)
    else:
        # Impacts distributed through the run, with varied magnitude so that
        # the severity classifier has a range to discriminate across.
        rng = np.random.default_rng(args.seed)
        n_imp = max(int(duration_hint / 6), 4)
        times = sorted(rng.uniform(1.0, max(duration_hint - 1.0, 2.0), n_imp).tolist())
        mags = rng.uniform(0.6, 2.8, n_imp).tolist()
        imu = IMUTrace.synthetic(duration_hint, rate_hz=100.0,
                                 impact_times=times, impact_magnitudes=mags,
                                 road_roughness_g=args.roughness, seed=args.seed)

    return ReplaySession(
        video_path=args.video, image_dir=args.images,
        track=track, imu=imu, fps=args.fps, max_frames=args.max_frames,
    )


def run_configuration(cfg_id: Configuration, args, session,
                      outage: Optional[tuple] = None) -> dict:
    """Execute one configuration over the replay session."""
    label = {Configuration.EDGE_FUSION: "A — edge + fusion",
             Configuration.CLOUD_ONLY: "B — cloud-only",
             Configuration.VISION_ONLY: "C — vision-only"}[cfg_id]
    print(f"  running configuration {label} ...", flush=True)

    db = f"data/exp_{cfg_id.value}.db"
    Path(db).unlink(missing_ok=True)

    pc = PipelineConfig(
        device_id=args.device_id,
        model_path=args.model,
        configuration=cfg_id,
        conf_threshold=args.conf,
        imgsz=args.imgsz,
        imu_threshold_g=args.imu_threshold,
        db_path=db,
        publish=args.publish,
        broker_host=args.broker,
    )
    if cfg_id is Configuration.EDGE_FUSION:
        pc.fusion_mode = FusionMode.GATE_AND_CORROBORATE

    pipe = Pipeline(pc)
    res_samples: List[dict] = []

    def tick(ev, rf):
        if rf.index % 20 == 0:
            s = sample_resources()
            if s:
                res_samples.append(s)

    stats = pipe.run(session, on_event=tick, outage=outage)
    pipe.close()

    out = stats.summary()
    out["configuration"] = cfg_id.value
    out["label"] = label

    if res_samples:
        for k in ("cpu_percent", "mem_percent", "cpu_temp_c"):
            vals = [r[k] for r in res_samples if k in r]
            if vals:
                out[k] = {"mean": round(statistics.fmean(vals), 2),
                          "max": round(max(vals), 2)}

    if pipe.buffer:
        out["buffer"] = pipe.buffer.stats()
        evs = pipe.buffer.all_events()
        out["clusters"] = len(cluster_events(evs, radius_m=args.cluster_radius))
        out["events"] = evs

    return out


# ----------------------------------------------------------------------
def offline_recovery_test(args, session, durations=(30, 60, 120)) -> List[dict]:
    """Exercise the store-and-forward mechanism across increasing outages."""
    print("\nOffline recovery test")
    results = []
    total = session.duration

    for d in durations:
        if d >= total:
            print(f"  {d}s outage exceeds replay duration ({total:.0f}s) — skipped")
            results.append({"outage_s": d, "skipped": True,
                            "reason": "replay shorter than outage"})
            continue

        start = max((total - d) / 2, 0.0)
        print(f"  {d}s outage from t={start:.1f}s ...", flush=True)

        db = f"data/outage_{d}.db"
        Path(db).unlink(missing_ok=True)
        pc = PipelineConfig(device_id=args.device_id, model_path=args.model,
                            configuration=Configuration.EDGE_FUSION,
                            conf_threshold=args.conf, imgsz=args.imgsz,
                            imu_threshold_g=args.imu_threshold, db_path=db,
                            publish=args.publish, broker_host=args.broker)
        pipe = Pipeline(pc)
        pipe.run(session, outage=(start, start + d))

        t0 = time.perf_counter()
        drained = (pipe.publisher.drain() if pipe.publisher
                   else {"recovered": 0,
                         "remaining": len(pipe.buffer.pending(1000))})
        elapsed = time.perf_counter() - t0

        bs = pipe.buffer.stats()
        generated = bs["total_events"]
        delivered = bs["acknowledged"]
        pipe.close()

        results.append({
            "outage_s": d,
            "events_generated": generated,
            "events_delivered": delivered,
            "events_lost": generated - delivered,
            "recovery_rate": round(delivered / generated, 4) if generated else None,
            "drain_time_s": round(elapsed, 3),
            "drain_rate_eps": round(drained.get("recovered", 0) / elapsed, 2)
                              if elapsed > 0 else None,
        })
    return results


# ----------------------------------------------------------------------
def write_tables(payload: dict, path: Path) -> None:
    """Emit Chapter 4 tables in markdown, ready to paste into the dissertation."""
    L: List[str] = []
    add = L.append
    cfgs = payload["configurations"]
    A = cfgs.get("A", {})
    B = cfgs.get("B", {})
    C = cfgs.get("C", {})

    add("# Chapter Four — Measured Results\n")
    add(f"Generated {payload['host']['timestamp']}  \n")
    dev = payload["host"].get("device_model", payload["host"]["machine"])
    add(f"Device: **{dev}**  \n")
    add(f"Model: `{payload['run']['model']}`  \n")
    add(f"Replay: {payload['run']['frames']} frames @ {payload['run']['fps']} fps "
        f"({payload['run']['duration_s']} s)\n")

    # ---- Table 4.3 ----
    add("\n## Table 4.3 — System performance results\n")
    add("| Metric | Measured | Unit |")
    add("|---|---|---|")
    il = A.get("inference_latency_ms") or {}
    add(f"| Inference latency (mean) | {il.get('mean','—')} | ms |")
    add(f"| Inference latency (p95) | {il.get('p95','—')} | ms |")
    add(f"| Sustained throughput | {A.get('throughput_fps','—')} | fps |")
    e2e = A.get("end_to_end_latency_ms") or {}
    add(f"| End-to-end latency (mean) | {e2e.get('mean','—')} | ms |")
    add(f"| End-to-end latency (p95) | {e2e.get('p95','—')} | ms |")
    cpu = A.get("cpu_percent") or {}
    add(f"| CPU utilisation (mean) | {cpu.get('mean','—')} | % |")
    add(f"| CPU utilisation (peak) | {cpu.get('max','—')} | % |")
    tmp = A.get("cpu_temp_c") or {}
    add(f"| CPU temperature (peak) | {tmp.get('max','—')} | °C |")
    mem = A.get("mem_percent") or {}
    add(f"| Memory utilisation (mean) | {mem.get('mean','—')} | % |")
    add(f"| Power consumption | *measure externally* | W |")
    add("\n*Power is recorded with an inline USB power meter; see bench/README.md.*\n")

    # ---- Gating ----
    add("\n## Gating effectiveness (Section 4.3.2)\n")
    add("| Metric | Value |")
    add("|---|---|")
    add(f"| Frames seen | {A.get('frames_seen','—')} |")
    add(f"| Frames inferred | {A.get('frames_inferred','—')} |")
    add(f"| Frames gated out | {A.get('frames_gated_out','—')} |")
    add(f"| Inference load reduction | {A.get('gating_reduction_pct','—')} % |")
    add(f"| Detections suppressed, no corroboration | "
        f"{A.get('suppressed_no_corroboration','—')} |")

    # ---- Table 4.5 ----
    add("\n## Table 4.5 — Baseline comparison\n")
    add("| Measure | A: edge+fusion | B: cloud-only | C: vision-only |")
    add("|---|---|---|---|")
    def row(name, key, sub=None):
        def g(d):
            v = d.get(key)
            if isinstance(v, dict) and sub:
                v = v.get(sub)
            return v if v is not None else "—"
        add(f"| {name} | {g(A)} | {g(B)} | {g(C)} |")
    row("Confirmed events", "confirmed_events")
    row("Raw detections", "raw_detections")
    row("Inference latency, mean (ms)", "inference_latency_ms", "mean")
    row("End-to-end latency, mean (ms)", "end_to_end_latency_ms", "mean")
    row("Throughput (fps)", "throughput_fps")
    row("Bytes transmitted", "bytes_transmitted")
    row("Data rate (kB/min)", "kb_per_minute")
    row("Frames inferred", "frames_inferred")

    # ---- significance ----
    if payload.get("significance"):
        add("\n## Paired t-tests (α = 0.05)\n")
        add("| Comparison | Metric | t | df | p | Cohen's d | Significant |")
        add("|---|---|---|---|---|---|---|")
        for s in payload["significance"]:
            r = s["result"]
            add(f"| {s['comparison']} | {s['metric']} | {r.get('t','—')} | "
                f"{r.get('df','—')} | {r.get('p','—')} | "
                f"{r.get('cohens_d','—')} | "
                f"{'yes' if r.get('significant_at_05') else 'no'} |")

    # ---- Table 4.4 ----
    if payload.get("offline_recovery"):
        add("\n## Table 4.4 — Communication reliability and offline recovery\n")
        add("| Outage (s) | Events generated | Delivered | Lost | Recovery rate | Drain (s) |")
        add("|---|---|---|---|---|---|")
        for r in payload["offline_recovery"]:
            if r.get("skipped"):
                add(f"| {r['outage_s']} | — | — | — | *{r['reason']}* | — |")
                continue
            add(f"| {r['outage_s']} | {r['events_generated']} | "
                f"{r['events_delivered']} | {r['events_lost']} | "
                f"{r['recovery_rate']} | {r['drain_time_s']} |")

    # ---- Table 4.3 severity ----
    add("\n## Severity distribution (Section 4.7)\n")
    sev = A.get("by_severity", {})
    tot = sum(sev.values()) or 1
    add("| Severity | Count | Percentage |")
    add("|---|---|---|")
    for k in ("low", "medium", "high"):
        n = sev.get(k, 0)
        add(f"| {k.capitalize()} | {n} | {round(100*n/tot,1)} % |")
    add(f"| **Total** | **{tot}** | **100 %** |")
    add(f"\nSpatial clusters after {payload['run']['cluster_radius']} m "
        f"aggregation: **{A.get('clusters','—')}**\n")

    add("\n---\n")
    add("*Detection accuracy metrics — precision, recall, F1, mAP — are produced "
        "separately by `bench/evaluate_model.py` against the annotated test set.*\n")

    path.write_text("\n".join(L), encoding="utf-8")


# ----------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Bench experiment for Chapter Four")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--video", help="replay video file")
    src.add_argument("--images", help="directory of road images")

    ap.add_argument("--model", required=True, help=".pt or .tflite model")
    ap.add_argument("--gpx", help="GPX track; synthetic Durban route if omitted")
    ap.add_argument("--imu-trace", help="recorded IMU .npy; synthetic if omitted")

    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--imu-threshold", type=float, default=0.45)
    ap.add_argument("--gps-jitter", type=float, default=2.5,
                    help="GNSS positional noise, metres std dev")
    ap.add_argument("--roughness", type=float, default=0.06,
                    help="baseline surface roughness, g")
    ap.add_argument("--cluster-radius", type=float, default=15.0)
    ap.add_argument("--repeats", type=int, default=3,
                    help="repeat runs for paired significance testing")
    ap.add_argument("--seed", type=int, default=42)

    ap.add_argument("--device-id", default="rpi-bench-01")
    ap.add_argument("--publish", action="store_true", help="publish over MQTT")
    ap.add_argument("--broker", default="localhost")
    ap.add_argument("--skip-outage", action="store_true")
    ap.add_argument("--out", default="results")
    args = ap.parse_args()

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    Path("data").mkdir(exist_ok=True)

    print("=" * 66)
    print("  Pothole detection framework — bench experiment")
    print("=" * 66)

    host = host_profile()
    print(f"\nHost: {host.get('device_model', host['machine'])}")
    print(f"Python {host['python']}  |  cores: {host.get('cpu_cores','?')}")

    session = build_session(args)
    print(f"\nReplay session: {json.dumps(session.summary(), indent=2)}")

    payload: dict = {
        "host": host,
        "run": {
            "model": args.model,
            "source": args.images or args.video,
            "frames": session.frame_count,
            "fps": session.fps,
            "duration_s": round(session.duration, 2),
            "conf_threshold": args.conf,
            "imu_threshold_g": args.imu_threshold,
            "cluster_radius": args.cluster_radius,
            "repeats": args.repeats,
            "seed": args.seed,
        },
        "configurations": {},
        "repeats": [],
        "significance": [],
    }

    print("\nBaseline comparison")
    for cid in (Configuration.EDGE_FUSION,
                Configuration.CLOUD_ONLY,
                Configuration.VISION_ONLY):
        payload["configurations"][cid.value] = run_configuration(cid, args, session)

    # Repeat runs supply the paired observations for significance testing
    if args.repeats > 1:
        print(f"\nRepeat runs for significance testing (n={args.repeats})")
        for r in range(args.repeats):
            print(f"  repeat {r+1}/{args.repeats}")
            rec = {}
            for cid in (Configuration.EDGE_FUSION,
                        Configuration.CLOUD_ONLY,
                        Configuration.VISION_ONLY):
                s = run_configuration(cid, args, session)
                rec[cid.value] = {
                    "inference_latency_ms": (s.get("inference_latency_ms") or {}).get("mean"),
                    "e2e_latency_ms": (s.get("end_to_end_latency_ms") or {}).get("mean"),
                    "throughput_fps": s.get("throughput_fps"),
                    "confirmed_events": s.get("confirmed_events"),
                    "bytes_transmitted": s.get("bytes_transmitted"),
                }
            payload["repeats"].append(rec)

        pairs = [("A", "B", "Edge+fusion vs cloud-only"),
                 ("A", "C", "Edge+fusion vs vision-only")]
        metrics = ["e2e_latency_ms", "inference_latency_ms",
                   "throughput_fps", "confirmed_events", "bytes_transmitted"]
        for x, y, lbl in pairs:
            for m in metrics:
                a = [r[x][m] for r in payload["repeats"] if r[x][m] is not None]
                b = [r[y][m] for r in payload["repeats"] if r[y][m] is not None]
                if len(a) >= 2 and len(b) >= 2:
                    payload["significance"].append({
                        "comparison": lbl, "metric": m,
                        "result": paired_t_test(a, b),
                    })

    if not args.skip_outage:
        payload["offline_recovery"] = offline_recovery_test(args, session)

    (outdir / "raw_measurements.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8")
    write_tables(payload, outdir / "chapter4_tables.md")

    A = payload["configurations"]["A"]
    print("\n" + "=" * 66)
    print("  Summary — Configuration A")
    print("=" * 66)
    print(f"  Frames seen        : {A['frames_seen']}")
    print(f"  Frames inferred    : {A['frames_inferred']} "
          f"({100-A['gating_reduction_pct']:.1f}% of frames)")
    print(f"  Confirmed events   : {A['confirmed_events']}")
    il = A.get("inference_latency_ms") or {}
    print(f"  Inference latency  : {il.get('mean','—')} ms mean, "
          f"{il.get('p95','—')} ms p95")
    print(f"  Throughput         : {A.get('throughput_fps','—')} fps")
    print(f"  Severity split     : {A.get('by_severity', {})}")
    print(f"\n  Written to {outdir}/chapter4_tables.md")
    print(f"            {outdir}/raw_measurements.json\n")


if __name__ == "__main__":
    main()
