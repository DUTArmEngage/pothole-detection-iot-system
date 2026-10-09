"""
demo.py — Live presentation demo.

Plays a road video through the full detection pipeline while showing what the
system is doing. Detections are real; only the vehicle's motion and position
are replayed.

Two windows are produced:

  DETECTION   the video with bounding boxes, confidence values and a status
              overlay showing frame rate, inference latency and event count
  DASHBOARD   run separately (dashboard/app.py) — the map fills as events arrive

Keys during playback
  SPACE   pause and resume
  N       when paused, advance one frame
  I       inject an inertial impact (use when a pothole is on screen)
  L       toggle the network link, to show offline buffering
  S       save the current frame
  Q       quit

Typical use
  python3 demo.py --video data/durban_drive.mp4 --model models/pothole.tflite
  python3 demo.py --video road.mp4 --model models/pothole.pt --speed 0.5
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "edge"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "harness"))

import cv2
import numpy as np

from detector import Detector
from severity import SeverityClassifier, Severity
from fusion import IMUFusion, FusionMode, IMUSample
from buffer import EventBuffer, RoadEvent
from replay import GPXTrack, IMUTrace, DURBAN_ROUTE

# Presentation colours (BGR)
COL = {
    Severity.HIGH:   (48, 48, 205),
    Severity.MEDIUM: (16, 137, 214),
    Severity.LOW:    (150, 150, 150),
}
WHITE = (255, 255, 255)
PANEL = (32, 32, 32)


def draw_panel(img, lines, x=12, y=12, w=330):
    """Semi-transparent status panel in the corner."""
    h = 22 * len(lines) + 16
    overlay = img.copy()
    cv2.rectangle(overlay, (x, y), (x + w, y + h), PANEL, -1)
    cv2.addWeighted(overlay, 0.72, img, 0.28, 0, img)
    cv2.rectangle(img, (x, y), (x + w, y + h), (90, 90, 90), 1)
    for i, (label, value, col) in enumerate(lines):
        yy = y + 26 + i * 22
        cv2.putText(img, label, (x + 12, yy),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.44, (170, 170, 170), 1, cv2.LINE_AA)
        cv2.putText(img, value, (x + 150, yy),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.50, col, 1, cv2.LINE_AA)


def draw_detection(img, det, sev, corroborated):
    """Box, label and severity band for one detection."""
    c = COL[sev.severity]
    x1, y1, x2, y2 = int(det.x1), int(det.y1), int(det.x2), int(det.y2)

    thickness = 3 if corroborated else 2
    cv2.rectangle(img, (x1, y1), (x2, y2), c, thickness)

    # Uncorroborated detections get a dashed top edge, so the two states are
    # visually distinct without needing a legend
    if not corroborated:
        for xd in range(x1, x2, 14):
            cv2.line(img, (xd, y1), (min(xd + 7, x2), y1), WHITE, 1)

    tag = f"{sev.severity.value.upper()}  {det.confidence:.2f}"
    (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    cv2.rectangle(img, (x1, y1 - th - 10), (x1 + tw + 12, y1), c, -1)
    cv2.putText(img, tag, (x1 + 6, y1 - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, WHITE, 1, cv2.LINE_AA)


def main():
    ap = argparse.ArgumentParser(description="Live presentation demo")
    ap.add_argument("--video", required=True, help="road video file")
    ap.add_argument("--model", required=True, help=".tflite or .pt model")
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--db", default="data/demo.db")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="playback rate; 0.5 runs at half speed for narration")
    ap.add_argument("--fresh", action="store_true", help="clear the database first")
    ap.add_argument("--width", type=int, default=1100, help="display width")
    ap.add_argument("--auto-imu", action="store_true",
                    help="generate inertial events automatically instead of by key")
    ap.add_argument("--record", help="write the annotated output to this file")
    args = ap.parse_args()

    if not os.path.exists(args.video):
        sys.exit(f"Video not found: {args.video}")

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        sys.exit(f"Could not open video: {args.video}")

    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    vw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = total / src_fps

    print(f"\n  Video     {Path(args.video).name}")
    print(f"            {vw}x{vh}, {total} frames, {duration:.1f} s at {src_fps:.1f} fps")
    print(f"  Model     {Path(args.model).name}")

    Path("data").mkdir(exist_ok=True)
    if args.fresh:
        Path(args.db).unlink(missing_ok=True)

    print("  Loading model ...", end=" ", flush=True)
    det = Detector(args.model, conf_threshold=args.conf, imgsz=args.imgsz)
    det.warmup()
    print("ready")

    sev_clf = SeverityClassifier(frame_area=vw * vh)
    fusion = IMUFusion(mode=FusionMode.CORROBORATE, trigger_threshold_g=0.45)
    buf = EventBuffer(args.db)
    track = GPXTrack.synthetic_route(DURBAN_ROUTE, duration, rate_hz=1.0)

    auto_imu = None
    if args.auto_imu:
        rng = np.random.default_rng(7)
        n = max(int(duration / 5), 3)
        times = sorted(rng.uniform(1, duration - 1, n).tolist())
        auto_imu = IMUTrace.synthetic(duration, 100.0, times,
                                      rng.uniform(0.8, 2.6, n).tolist())

    writer = None
    if args.record:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(args.record, fourcc, src_fps, (vw, vh))

    cv2.namedWindow("DETECTION", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("DETECTION", args.width, int(args.width * vh / vw))

    print("\n  SPACE pause   N step   I impact   L link   S save   Q quit\n")

    idx = 0
    events = 0
    buffered = 0
    link_up = True
    paused = False
    lat_hist = deque(maxlen=30)
    fps_hist = deque(maxlen=30)
    impact_until = 0.0
    t_wall = time.perf_counter()

    while True:
        if not paused:
            ok, frame = cap.read()
            if not ok:
                break
            t_video = idx / src_fps
            t0 = time.perf_counter()

            # Inertial input: automatic trace, or manual key press
            if auto_imu is not None:
                for s in auto_imu.between(t_video - 1 / src_fps, t_video + 1 / src_fps):
                    fusion.add_sample(IMUSample(t=s[0], ax=s[1], ay=s[2], az=s[3]))
            elif time.perf_counter() < impact_until:
                fusion.add_sample(IMUSample(t=t_video, ax=0.4, ay=0.3, az=2.7))
            else:
                fusion.add_sample(IMUSample(t=t_video, ax=0.01, ay=0.01, az=1.01))

            result = det.infer(frame)
            lat_hist.append(result.latency_ms)

            decision = fusion.evaluate(t_video)
            fix = track.at(t_video)

            for d in result.detections:
                s = sev_clf.classify(d.area, d.confidence, decision.peak_g)
                draw_detection(frame, d, s, decision.corroborated)

                ev = RoadEvent.new(
                    device_id="demo-rig",
                    latitude=round(fix.latitude, 7),
                    longitude=round(fix.longitude, 7),
                    confidence=round(d.confidence, 4),
                    severity=s.severity.value,
                    severity_score=s.score,
                    defect_class="pothole",
                    box=[d.x1, d.y1, d.x2, d.y2],
                    peak_impact_g=decision.peak_g,
                    speed_kph=round(fix.speed_kph, 1),
                )
                buf.enqueue(ev)
                if link_up:
                    buf.acknowledge(ev.event_id)
                else:
                    buffered += 1
                events += 1

            fps_hist.append(1.0 / max(time.perf_counter() - t0, 1e-6))
            idx += 1

        mean_lat = np.mean(lat_hist) if lat_hist else 0.0
        mean_fps = np.mean(fps_hist) if fps_hist else 0.0

        panel = [
            ("FRAME",     f"{idx}/{total}", WHITE),
            ("INFERENCE", f"{mean_lat:6.1f} ms", (120, 220, 120)),
            ("RATE",      f"{mean_fps:6.1f} fps", (120, 220, 120)),
            ("POSITION",  f"{fix.latitude:.5f}, {fix.longitude:.5f}", (200, 200, 120)),
            ("SPEED",     f"{fix.speed_kph:.0f} km/h", WHITE),
            ("IMU",       "IMPACT" if decision.corroborated else "quiet",
                          (60, 200, 255) if decision.corroborated else (140, 140, 140)),
            ("LINK",      "ONLINE" if link_up else f"OFFLINE ({buffered} held)",
                          (120, 220, 120) if link_up else (60, 60, 235)),
            ("EVENTS",    f"{events}", WHITE),
        ]
        draw_panel(frame, panel)

        if paused:
            cv2.putText(frame, "PAUSED", (vw - 150, 42),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (60, 60, 235), 2, cv2.LINE_AA)

        cv2.imshow("DETECTION", frame)
        if writer is not None and not paused:
            writer.write(frame)

        delay = max(int(1000 / (src_fps * args.speed)), 1)
        k = cv2.waitKey(1 if paused else delay) & 0xFF

        if k == ord("q"):
            break
        elif k == ord(" "):
            paused = not paused
        elif k == ord("n") and paused:
            paused = False
            continue
        elif k == ord("i"):
            impact_until = time.perf_counter() + 0.5
            print("  impact injected")
        elif k == ord("l"):
            link_up = not link_up
            print(f"  link {'restored' if link_up else 'dropped'}")
            if link_up and buffered:
                for row in buf.pending(limit=500):
                    buf.acknowledge(row["event_id"])
                print(f"  {buffered} buffered events delivered")
                buffered = 0
        elif k == ord("s"):
            Path("data/frames").mkdir(parents=True, exist_ok=True)
            out = f"data/frames/frame_{idx:05d}.jpg"
            cv2.imwrite(out, frame)
            print(f"  saved {out}")

    cap.release()
    if writer is not None:
        writer.release()
    cv2.destroyAllWindows()

    st = buf.stats()
    print("\n" + "=" * 52)
    print("  Demo complete")
    print("=" * 52)
    print(f"  Frames processed    {idx}")
    print(f"  Events recorded     {st['total_events']}")
    print(f"  By severity         {st['by_severity']}")
    print(f"  Mean inference      {np.mean(lat_hist):.1f} ms"
          if lat_hist else "  Mean inference      —")
    print(f"  Mean rate           {np.mean(fps_hist):.1f} fps"
          if fps_hist else "  Mean rate           —")
    print(f"\n  Map: python3 dashboard/app.py --db {args.db}\n")


if __name__ == "__main__":
    main()
