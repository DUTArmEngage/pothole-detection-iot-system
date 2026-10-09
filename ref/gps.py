"""
gps.py — NEO-6M GNSS receiver over the Raspberry Pi serial port.

Reads NMEA 0183 sentences from a u-blox NEO-6M module, validates them, and
exposes a position fix in the form the pipeline consumes. Depends on nothing
outside the standard library; pyserial is used if present but is not required.

THE DISTINCTION THAT MATTERS MOST
    A GNSS receiver emits sentences continuously whether or not it has a fix.
    Before the first fix the sentences arrive with their position fields empty.
    So there are two separate questions, and they fail for different reasons:

        Is it WIRED correctly?   Are NMEA sentences arriving at all?
        Does it have a FIX?      Are those sentences carrying coordinates?

    Wiring is testable indoors in seconds. A fix is not: the receiver needs a
    view of the sky, and a cold start typically takes 30 s to several minutes
    even outdoors. Indoors, under a roof, no fix is the expected result and is
    not evidence of a fault. --diagnose answers the wiring question only, and
    says so.

WIRING
    NEO-6M VCC  ->  Pi pin 2 or 4   (5V)
    NEO-6M GND  ->  Pi pin 6        (ground)
    NEO-6M TX   ->  Pi pin 10       (GPIO15, the Pi's RX)
    NEO-6M RX   ->  Pi pin 8        (GPIO14, the Pi's TX)

    The data lines cross. Transmit on one device goes to receive on the other.
    Wiring TX to TX is the single most common cause of a silent module.

PI CONFIGURATION
    The serial port must be released by the login console and enabled as
    hardware. raspi-config -> Interface Options -> Serial Port:
        login shell over serial?   NO
        serial port hardware?      YES

    On a Pi 4, /dev/serial0 points by default at the mini UART, whose baud rate
    is tied to the variable core clock and can therefore drift. Adding
    'dtoverlay=disable-bt' to /boot/firmware/config.txt reassigns the stable
    PL011 controller to the GPIO header. --diagnose checks all of this.

USAGE
    python3 -m edge.gps --diagnose      is it wired and talking?
    python3 -m edge.gps --raw           dump sentences exactly as they arrive
    python3 -m edge.gps --monitor       live fix status, satellites, position
    python3 -m edge.gps --verify        timed acceptance test, writes JSON
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import termios
import time
import tty
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

DEFAULT_PORT = "/dev/serial0"
DEFAULT_BAUD = 9600            # NEO-6M factory default
BAUD_CANDIDATES = (9600, 38400, 4800, 19200, 57600, 115200)

NMEA_RE = re.compile(rb"^\$(G[PNLAB][A-Z]{3}),(.*)\*([0-9A-Fa-f]{2})\s*$")


class GPSError(RuntimeError):
    """Raised when the receiver cannot be reached or does not respond."""


# ---------------------------------------------------------------------------
# Serial transport
# ---------------------------------------------------------------------------

_BAUD_CONSTANTS = {
    4800: termios.B4800, 9600: termios.B9600, 19200: termios.B19200,
    38400: termios.B38400, 57600: termios.B57600, 115200: termios.B115200,
}


class SerialPort:
    """
    Minimal 8N1 serial reader built on termios.

    pyserial does the same thing and more, but it is not installed by default
    on Raspberry Pi OS and PEP 668 makes installing it into the system Python
    awkward. Since only blocking reads at a fixed line discipline are needed,
    the standard library suffices.
    """

    def __init__(self, port: str = DEFAULT_PORT, baud: int = DEFAULT_BAUD,
                 timeout: float = 1.0) -> None:
        if baud not in _BAUD_CONSTANTS:
            raise ValueError(f"unsupported baud {baud}")
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._fd: Optional[int] = None
        self._buf = b""

    def open(self) -> "SerialPort":
        if not os.path.exists(self.port):
            raise GPSError(
                f"{self.port} does not exist. The serial port is not enabled. "
                f"Run sudo raspi-config, then Interface Options, Serial Port: "
                f"answer No to the login shell and Yes to the hardware port, "
                f"then reboot."
            )
        try:
            self._fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY)
        except PermissionError as exc:
            raise GPSError(
                f"No permission to open {self.port}. Add the user to the "
                f"dialout group: sudo usermod -aG dialout $USER, then log out "
                f"and back in."
            ) from exc

        attrs = termios.tcgetattr(self._fd)
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs

        # Raw 8N1, no flow control, receiver enabled, ignore modem lines.
        iflag = termios.IGNPAR
        oflag = 0
        lflag = 0
        cflag = termios.CS8 | termios.CREAD | termios.CLOCAL
        cc = list(cc)
        cc[termios.VMIN] = 0
        cc[termios.VTIME] = max(1, int(self.timeout * 10))   # decisecond units
        speed = _BAUD_CONSTANTS[self.baud]

        termios.tcsetattr(self._fd, termios.TCSANOW,
                          [iflag, oflag, cflag, lflag, speed, speed, cc])
        termios.tcflush(self._fd, termios.TCIFLUSH)
        return self

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "SerialPort":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    def read_bytes(self, n: int = 512) -> bytes:
        assert self._fd is not None
        try:
            return os.read(self._fd, n)
        except OSError:
            return b""

    def read_line(self, deadline: Optional[float] = None) -> Optional[bytes]:
        """Return one newline-terminated line, or None if the deadline passes."""
        while True:
            if b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                return line.strip()
            if deadline is not None and time.time() > deadline:
                return None
            chunk = self.read_bytes()
            if not chunk:
                if deadline is None:
                    return None
                continue
            self._buf += chunk
            if len(self._buf) > 8192:          # runaway guard on a noisy line
                self._buf = self._buf[-1024:]


# ---------------------------------------------------------------------------
# NMEA parsing
# ---------------------------------------------------------------------------

FIX_QUALITY = {
    0: "no fix", 1: "GPS fix", 2: "DGPS fix", 3: "PPS fix",
    4: "RTK fixed", 5: "RTK float", 6: "dead reckoning",
}


def nmea_checksum_ok(raw: bytes) -> bool:
    """Validate the XOR checksum between '$' and '*'."""
    m = NMEA_RE.match(raw)
    if not m:
        return False
    body = raw[1:raw.rindex(b"*")]
    want = int(m.group(3), 16)
    got = 0
    for b in body:
        got ^= b
    return got == want


def _dm_to_degrees(value: str, hemi: str) -> Optional[float]:
    """Convert NMEA ddmm.mmmm to signed decimal degrees."""
    if not value or not hemi:
        return None
    try:
        dot = value.index(".")
    except ValueError:
        return None
    deg = float(value[:dot - 2])
    minutes = float(value[dot - 2:])
    dec = deg + minutes / 60.0
    return -dec if hemi.upper() in ("S", "W") else dec


@dataclass
class GPSFix:
    """One position solution, or the absence of one."""
    t: float = 0.0
    utc: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    altitude_m: Optional[float] = None
    quality: int = 0
    satellites_used: int = 0
    satellites_visible: int = 0
    hdop: Optional[float] = None
    speed_kmh: Optional[float] = None
    heading_deg: Optional[float] = None

    @property
    def has_fix(self) -> bool:
        return self.quality > 0 and self.lat is not None and self.lon is not None

    @property
    def quality_text(self) -> str:
        return FIX_QUALITY.get(self.quality, f"unknown ({self.quality})")

    def describe(self) -> str:
        if not self.has_fix:
            return (f"no fix — {self.satellites_visible} satellites visible, "
                    f"{self.satellites_used} used")
        return (f"{self.lat:.6f}, {self.lon:.6f}  "
                f"alt {self.altitude_m:.0f} m  "
                f"{self.satellites_used} sats  HDOP {self.hdop}")


class NMEAParser:
    """Accumulates sentences into a current fix."""

    def __init__(self) -> None:
        self.fix = GPSFix()
        self.counts: Dict[str, int] = {}
        self.valid = 0
        self.invalid = 0

    def feed(self, raw: bytes) -> Optional[str]:
        """Parse one line. Returns the sentence type, or None if unusable."""
        m = NMEA_RE.match(raw)
        if not m:
            self.invalid += 1
            return None
        if not nmea_checksum_ok(raw):
            self.invalid += 1
            return None

        self.valid += 1
        talker = m.group(1).decode()
        kind = talker[2:]
        self.counts[talker] = self.counts.get(talker, 0) + 1
        f = m.group(2).decode(errors="replace").split(",")
        self.fix.t = time.time()

        try:
            if kind == "GGA" and len(f) >= 9:
                self.fix.utc = f[0] or None
                self.fix.lat = _dm_to_degrees(f[1], f[2])
                self.fix.lon = _dm_to_degrees(f[3], f[4])
                self.fix.quality = int(f[5]) if f[5] else 0
                self.fix.satellites_used = int(f[6]) if f[6] else 0
                self.fix.hdop = float(f[7]) if f[7] else None
                self.fix.altitude_m = float(f[8]) if f[8] else None
            elif kind == "RMC" and len(f) >= 8:
                if f[1] == "A":
                    self.fix.lat = _dm_to_degrees(f[2], f[3])
                    self.fix.lon = _dm_to_degrees(f[4], f[5])
                self.fix.speed_kmh = float(f[6]) * 1.852 if f[6] else None
                self.fix.heading_deg = float(f[7]) if f[7] else None
            elif kind == "GSV" and len(f) >= 3:
                self.fix.satellites_visible = int(f[2]) if f[2] else 0
            elif kind == "GSA" and len(f) >= 16:
                self.fix.hdop = float(f[15]) if f[15] else self.fix.hdop
        except (ValueError, IndexError):
            pass        # a partially populated sentence is normal before a fix

        return talker


# ---------------------------------------------------------------------------
# Receiver
# ---------------------------------------------------------------------------

class NEO6M:
    """A NEO-6M receiver on a serial port."""

    def __init__(self, port: str = DEFAULT_PORT, baud: int = DEFAULT_BAUD) -> None:
        self.serial = SerialPort(port, baud)
        self.parser = NMEAParser()

    def __enter__(self) -> "NEO6M":
        self.serial.open()
        return self

    def __exit__(self, *exc) -> None:
        self.serial.close()

    def sentences(self, seconds: Optional[float] = None):
        """Yield raw sentences as they arrive."""
        deadline = time.time() + seconds if seconds else None
        while deadline is None or time.time() < deadline:
            line = self.serial.read_line(deadline=deadline or time.time() + 2)
            if line:
                yield line

    def read_fix(self, timeout: float = 2.0) -> GPSFix:
        """Consume sentences until the fix state is refreshed by a GGA."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.serial.read_line(deadline=deadline)
            if line and self.parser.feed(line) and line[3:6] == b"GGA":
                break
        return self.parser.fix

    def wait_for_fix(self, timeout: float = 300.0, on_update=None) -> GPSFix:
        """Block until the receiver reports a position, or the timeout passes."""
        start = time.time()
        while time.time() - start < timeout:
            fix = self.read_fix(timeout=2.0)
            if on_update:
                on_update(fix, time.time() - start)
            if fix.has_fix:
                return fix
        return self.parser.fix


# ---------------------------------------------------------------------------
# Configuration inspection
# ---------------------------------------------------------------------------

def _read_first(paths: List[str]) -> Tuple[Optional[str], Optional[str]]:
    for p in paths:
        if os.path.exists(p):
            try:
                with open(p) as fh:
                    return p, fh.read()
            except OSError:
                pass
    return None, None


def inspect_pi_config() -> Dict[str, object]:
    """
    Report the serial configuration that governs whether the port is usable.

    Three settings matter and each fails differently. The login console holding
    the port means the kernel is both writing to and reading from it, so the
    receiver's sentences are consumed before any program sees them. enable_uart
    off means there is no port. The mini UART being mapped to the header means
    the port exists but its baud rate drifts with the core clock, which shows up
    as intermittent corruption rather than silence.
    """
    out: Dict[str, object] = {}

    path, cmdline = _read_first(["/boot/firmware/cmdline.txt", "/boot/cmdline.txt"])
    out["cmdline_path"] = path
    out["console_on_serial"] = bool(
        cmdline and re.search(r"console=(serial0|ttyAMA0|ttyS0)", cmdline))

    path, config = _read_first(["/boot/firmware/config.txt", "/boot/config.txt"])
    out["config_path"] = path
    config = config or ""
    out["enable_uart"] = bool(re.search(r"^\s*enable_uart\s*=\s*1", config,
                                        re.M))
    out["disable_bt"] = bool(re.search(r"^\s*dtoverlay\s*=\s*disable-bt",
                                       config, re.M))

    for link in ("/dev/serial0", "/dev/serial1"):
        if os.path.islink(link):
            out[link] = os.path.basename(os.readlink(link))
        elif os.path.exists(link):
            out[link] = "present (not a symlink)"
        else:
            out[link] = None

    return out


def _console_holding_port() -> List[str]:
    """Name any getty service that would be competing for the serial port."""
    found = []
    base = "/etc/systemd/system"
    for root, _dirs, files in os.walk(base):
        for f in files:
            if "serial-getty" in f and "ttyS0" in f or "serial-getty" in f and "ttyAMA0" in f:
                found.append(os.path.join(root, f))
    return found


# ---------------------------------------------------------------------------
# Command-line modes
# ---------------------------------------------------------------------------

def _diagnose(args) -> int:
    """
    Establish whether the receiver is wired and transmitting. This does not
    test whether it has a position fix, which requires sky and time.
    """
    print(f"Diagnosing {args.port} at {args.baud} baud\n")

    print("1  Pi serial configuration")
    cfg = inspect_pi_config()
    ok = True
    if cfg["console_on_serial"]:
        ok = False
        print(f"   FAIL  a login console is attached to the serial port")
        print(f"         ({cfg['cmdline_path']} contains console=serial0).")
        print(f"         The kernel consumes the receiver's output before any")
        print(f"         program can read it. Disable it with:")
        print(f"           sudo raspi-config  ->  Interface Options  ->  "
              f"Serial Port")
        print(f"           login shell over serial?  No")
        print(f"           serial port hardware?     Yes")
    else:
        print(f"   ok    no login console on the serial port")

    if cfg["enable_uart"]:
        print(f"   ok    enable_uart=1 in {cfg['config_path']}")
    else:
        print(f"   warn  enable_uart=1 not found in {cfg['config_path']}. "
              f"On some images the port still works; if nothing arrives "
              f"below, add it and reboot.")

    mapped = cfg.get("/dev/serial0")
    if mapped is None:
        print(f"   FAIL  /dev/serial0 does not exist — the port is not "
              f"enabled yet.")
    else:
        print(f"   ok    /dev/serial0 -> {mapped}")
    if mapped == "ttyS0":
        print(f"   note  that is the mini UART, whose baud rate follows the "
              f"core clock and can drift under load.")
        print(f"         If sentences arrive but are intermittently corrupted, "
              f"add  dtoverlay=disable-bt  to")
        print(f"         {cfg['config_path']} and reboot to move the port to "
              f"the stable PL011 controller.")
    elif mapped == "ttyAMA0":
        print(f"   ok    that is the PL011 controller — the stable one")

    if not ok:
        return 2

    print("\n2  opening the port")
    try:
        port = SerialPort(args.port, args.baud, timeout=1.0).open()
    except GPSError as exc:
        print(f"   FAIL  {exc}")
        return 2
    print(f"   ok    opened at {args.baud} 8N1")

    print(f"\n3  listening for {args.seconds:.0f} s")
    raw = b""
    end = time.time() + args.seconds
    while time.time() < end:
        raw += port.read_bytes(512)
    port.close()

    if not raw:
        print(f"   FAIL  nothing arrived at all.\n")
        print(f"   Silence means the module is not transmitting into this pin. "
              f"In order of likelihood:")
        print(f"     1  TX and RX are not crossed. The module's TX must go to "
              f"Pi pin 10, not pin 8.")
        print(f"     2  no power. The module's LED should be lit; it blinks "
              f"once a second only after a fix.")
        print(f"     3  ground is not shared between the module and the Pi.")
        print(f"     4  the wrong baud rate — rerun with --sweep to try the "
              f"others.")
        return 2

    printable = sum(1 for b in raw if 32 <= b < 127 or b in (10, 13))
    ratio = printable / len(raw)
    print(f"   ok    {len(raw)} bytes arrived "
          f"({ratio * 100:.0f}% printable)")

    if ratio < 0.85:
        print(f"\n   The data is mostly non-printable, which means bytes are "
              f"arriving but the baud rate is wrong.")
        print(f"   NMEA is plain text, so a correctly tuned port produces "
              f"readable sentences.")
        print(f"   Run:  python3 -m edge.gps --sweep")
        print(f"\n   first bytes: {raw[:48]!r}")
        return 1

    print("\n4  validating NMEA")
    parser = NMEAParser()
    for line in raw.split(b"\n"):
        line = line.strip()
        if line:
            parser.feed(line)

    total = parser.valid + parser.invalid
    print(f"   ok    {parser.valid} valid sentences, {parser.invalid} "
          f"unusable ({parser.valid / total * 100:.0f}% clean)"
          if total else "   warn  no complete sentences in the window")
    if parser.counts:
        kinds = ", ".join(f"{k} x{v}" for k, v in sorted(parser.counts.items()))
        print(f"         {kinds}")
    if parser.invalid > parser.valid:
        print(f"   warn  more sentences failed than passed. If /dev/serial0 "
              f"maps to ttyS0, this is the mini UART drifting — see above.")

    fix = parser.fix
    print()
    if parser.valid == 0:
        print("   Bytes arrive and look like text, but no sentence passed its "
              "checksum.")
        print("   Check the baud rate first with --sweep.")
        return 1

    print(f"   PASS  the receiver is wired correctly and transmitting.")
    print(f"         {parser.valid} valid NMEA sentences in {args.seconds:.0f} s")
    print()
    print(f"   Fix status: {fix.quality_text}")
    if fix.has_fix:
        print(f"   Position:   {fix.lat:.6f}, {fix.lon:.6f}  "
              f"altitude {fix.altitude_m:.0f} m")
        print(f"   Satellites: {fix.satellites_used} used, "
              f"{fix.satellites_visible} visible,  HDOP {fix.hdop}")
    else:
        print(f"   Satellites: {fix.satellites_used} used, "
              f"{fix.satellites_visible} visible")
        print()
        print(f"   No position yet, which is the expected result indoors and "
              f"is NOT a wiring fault.")
        print(f"   The wiring test above has already passed. For a fix the "
              f"antenna needs a view of the sky;")
        print(f"   a cold start typically takes 30 s to a few minutes. Put it "
              f"at a window or outside and run:")
        print(f"     python3 -m edge.gps --monitor")
    return 0


def _sweep(args) -> int:
    """Try each candidate baud rate and report which produces valid NMEA."""
    print(f"Sweeping baud rates on {args.port}, {args.dwell:.0f} s each\n")
    results = []
    for baud in BAUD_CANDIDATES:
        try:
            port = SerialPort(args.port, baud, timeout=0.5).open()
        except GPSError as exc:
            print(f"  FAIL  {exc}")
            return 2
        raw = b""
        end = time.time() + args.dwell
        while time.time() < end:
            raw += port.read_bytes(512)
        port.close()

        parser = NMEAParser()
        for line in raw.split(b"\n"):
            line = line.strip()
            if line:
                parser.feed(line)
        results.append((baud, len(raw), parser.valid))
        mark = "  <-- valid NMEA" if parser.valid else ""
        print(f"  {baud:>6} baud   {len(raw):>5} bytes   "
              f"{parser.valid:>3} good sentences{mark}")

    best = max(results, key=lambda r: r[2])
    print()
    if best[2] == 0:
        print("  No baud rate produced valid sentences.")
        print("  Bytes were " + ("arriving, so the wiring is live but the data "
                                 "is not NMEA — check that the module really "
                                 "is a NEO-6M."
                                 if any(r[1] for r in results) else
                                 "never arriving, so this is a wiring or power "
                                 "fault rather than a baud mismatch. Confirm "
                                 "the module's TX goes to Pi pin 10."))
        return 1
    print(f"  Use --baud {best[0]}")
    if best[0] != DEFAULT_BAUD:
        print(f"  The module is not at its factory default of "
              f"{DEFAULT_BAUD}. Record this in the build notes so the "
              f"pipeline configuration matches.")
    return 0


def _raw(args) -> int:
    """Dump sentences exactly as they arrive, with checksum status."""
    print(f"Reading {args.port} at {args.baud} baud. Ctrl-C to stop.\n")
    try:
        with NEO6M(args.port, args.baud) as gps:
            for line in gps.sentences():
                ok = nmea_checksum_ok(line)
                mark = " " if ok else "x"
                print(f"{mark} {line.decode(errors='replace')}")
    except KeyboardInterrupt:
        print("\nStopped.")
    except GPSError as exc:
        print(f"FAIL  {exc}")
        return 2
    return 0


def _monitor(args) -> int:
    """Live fix status. Intended to be left running while acquiring a fix."""
    print(f"Monitoring {args.port} at {args.baud} baud. Ctrl-C to stop.")
    print("A cold start outdoors typically takes 30 s to a few minutes.\n")
    start = time.time()
    first_fix_at: Optional[float] = None
    try:
        with NEO6M(args.port, args.baud) as gps:
            while True:
                fix = gps.read_fix(timeout=3.0)
                elapsed = time.time() - start
                if fix.has_fix and first_fix_at is None:
                    first_fix_at = elapsed
                    print(f"\n*** first fix after {elapsed:.0f} s ***")

                bar = "#" * min(20, fix.satellites_visible)
                if fix.has_fix:
                    line = (f"{elapsed:6.0f}s  FIX   "
                            f"{fix.lat:+.6f} {fix.lon:+.6f}  "
                            f"alt {fix.altitude_m or 0:5.0f}m  "
                            f"sats {fix.satellites_used:2d}  "
                            f"HDOP {fix.hdop or 0:4.1f}")
                else:
                    line = (f"{elapsed:6.0f}s  searching  "
                            f"visible {fix.satellites_visible:2d} "
                            f"used {fix.satellites_used:2d}  {bar}")
                print(line.ljust(78), end="\n" if args.lines else "\r",
                      flush=True)
    except KeyboardInterrupt:
        print("\nStopped.")
        if first_fix_at is not None:
            print(f"Time to first fix: {first_fix_at:.0f} s")
    except GPSError as exc:
        print(f"FAIL  {exc}")
        return 2
    return 0


def _verify(args) -> int:
    """
    Acceptance test for Section 4.2.

    Records time to first fix, the satellite and HDOP distribution once fixed,
    the sentence rate, and the static position scatter. The scatter is the
    receiver's repeatability at a stationary point — it is not an accuracy
    figure, because that would require a surveyed reference coordinate, which
    this evaluation does not have. The distinction is stated in the output so
    it does not get overstated later.
    """
    out: Dict[str, object] = {"port": args.port, "baud": args.baud}
    print("GNSS acceptance test")
    print("The receiver needs a clear view of the sky. Indoors this will time "
          "out, which is not a fault.\n")

    try:
        with NEO6M(args.port, args.baud) as gps:
            print(f"Phase 1 of 2 — waiting for first fix "
                  f"(up to {args.fix_timeout:.0f} s)")
            start = time.time()
            last_report = 0.0

            def progress(fix: GPSFix, elapsed: float) -> None:
                nonlocal last_report
                if elapsed - last_report >= 5:
                    last_report = elapsed
                    print(f"   {elapsed:5.0f}s  visible "
                          f"{fix.satellites_visible:2d}  used "
                          f"{fix.satellites_used:2d}", end="\r", flush=True)

            fix = gps.wait_for_fix(timeout=args.fix_timeout, on_update=progress)
            ttff = time.time() - start

            if not fix.has_fix:
                print(f"\n   No fix within {args.fix_timeout:.0f} s. "
                      f"{fix.satellites_visible} satellites were visible.")
                print(f"   Sentences are arriving, so the receiver is working; "
                      f"it cannot see enough sky.")
                out["result"] = "no_fix"
                out["satellites_visible"] = fix.satellites_visible
                out["wait_seconds"] = ttff
            else:
                print(f"\n   fix acquired after {ttff:.0f} s — "
                      f"{fix.describe()}")
                out["time_to_first_fix_s"] = ttff

                print(f"\nPhase 2 of 2 — logging {args.duration:.0f} s at rest")
                lats, lons, alts, hdops, sats = [], [], [], [], []
                sentences = 0
                end = time.time() + args.duration
                while time.time() < end:
                    f = gps.read_fix(timeout=2.0)
                    sentences = gps.parser.valid
                    if f.has_fix:
                        lats.append(f.lat)
                        lons.append(f.lon)
                        if f.altitude_m is not None:
                            alts.append(f.altitude_m)
                        if f.hdop is not None:
                            hdops.append(f.hdop)
                        sats.append(f.satellites_used)

                if len(lats) >= 2:
                    mlat = statistics.fmean(lats)
                    mlon = statistics.fmean(lons)
                    # Convert degree scatter to metres at this latitude.
                    m_per_deg_lat = 111_320.0
                    m_per_deg_lon = 111_320.0 * abs(
                        __import__("math").cos(__import__("math").radians(mlat)))
                    dev = [
                        (((la - mlat) * m_per_deg_lat) ** 2 +
                         ((lo - mlon) * m_per_deg_lon) ** 2) ** 0.5
                        for la, lo in zip(lats, lons)
                    ]
                    out["static_scatter"] = {
                        "n": len(lats),
                        "mean_latitude": mlat,
                        "mean_longitude": mlon,
                        "rms_m": (statistics.fmean(d * d for d in dev)) ** 0.5,
                        "max_m": max(dev),
                        "note": ("Repeatability about the receiver's own mean "
                                 "position, not accuracy against a surveyed "
                                 "reference."),
                    }
                    print(f"   scatter about the mean position: "
                          f"RMS {out['static_scatter']['rms_m']:.2f} m, "
                          f"max {max(dev):.2f} m")

                if hdops:
                    out["hdop"] = {"mean": statistics.fmean(hdops),
                                   "min": min(hdops), "max": max(hdops)}
                    print(f"   HDOP mean {out['hdop']['mean']:.2f} "
                          f"(range {min(hdops):.2f}–{max(hdops):.2f})")
                if sats:
                    out["satellites_used"] = {"mean": statistics.fmean(sats),
                                              "min": min(sats), "max": max(sats)}
                    print(f"   satellites used: mean "
                          f"{statistics.fmean(sats):.1f}, "
                          f"min {min(sats)}, max {max(sats)}")
                if alts:
                    out["altitude_m"] = {"mean": statistics.fmean(alts)}

                out["sentence_rate_hz"] = sentences / max(1e-9, args.duration)
                out["result"] = "pass"
    except GPSError as exc:
        print(f"FAIL  {exc}")
        return 2

    out["valid_sentences"] = None
    os.makedirs(args.results_dir, exist_ok=True)
    path = os.path.join(args.results_dir, "gps_verification.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=2)
    print(f"\n  written to {path}")
    return 0 if out.get("result") == "pass" else 1


def main() -> int:
    p = argparse.ArgumentParser(description="NEO-6M GNSS receiver")
    p.add_argument("--port", default=DEFAULT_PORT,
                   help=f"serial device (default {DEFAULT_PORT})")
    p.add_argument("--baud", type=int, default=DEFAULT_BAUD,
                   choices=sorted(BAUD_CANDIDATES),
                   help=f"baud rate (default {DEFAULT_BAUD})")
    p.add_argument("--seconds", type=float, default=5.0,
                   help="listening window for --diagnose")
    p.add_argument("--dwell", type=float, default=3.0,
                   help="seconds per rate during --sweep")
    p.add_argument("--duration", type=float, default=120.0,
                   help="logging period for --verify")
    p.add_argument("--fix-timeout", type=float, default=300.0,
                   help="how long --verify waits for the first fix")
    p.add_argument("--lines", action="store_true",
                   help="one line per update instead of updating in place")
    p.add_argument("--results-dir", default="results")

    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--diagnose", action="store_true")
    mode.add_argument("--sweep", action="store_true")
    mode.add_argument("--raw", action="store_true")
    mode.add_argument("--monitor", action="store_true")
    mode.add_argument("--verify", action="store_true")

    args = p.parse_args()

    if args.sweep:
        return _sweep(args)
    if args.raw:
        return _raw(args)
    if args.monitor:
        return _monitor(args)
    if args.verify:
        return _verify(args)
    return _diagnose(args)


if __name__ == "__main__":
    raise SystemExit(main())
