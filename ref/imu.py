"""
imu.py — MPU-6050 inertial reader.

Talks to the MPU-6050 (GY-521) over an I2C character device using raw ioctl,
so it depends on nothing outside the standard library. smbus2 is not required.

BUS SELECTION
    The default is /dev/i2c-3, not the usual /dev/i2c-1. On the prototype unit
    the hardware I2C controller on GPIO2/GPIO3 does not respond, so the bus is
    provided in software by dtoverlay=i2c-gpio on GPIO23 (SDA) and GPIO24
    (SCL). This is recorded as a build deviation in Section 3.14.1. Software
    I2C is bit-banged by the CPU and therefore costs more processor time than
    the hardware controller would; Section 4.9 notes this when reporting
    utilisation.

    Override with --bus if the unit is rebuilt on working hardware pins.

USAGE
    python3 -m edge.imu --probe              confirm the device answers
    python3 -m edge.imu --stream             live readings, Ctrl-C to stop
    python3 -m edge.imu --verify             30 s acceptance test, writes JSON
    python3 -m edge.imu --diagnose           isolate a wiring or bus fault
"""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import json
import math
import os
import statistics
import struct
import time
from dataclasses import dataclass, asdict
from typing import List, Optional

try:
    from .fusion import IMUSample
except ImportError:                                   # direct execution
    from dataclasses import dataclass as _dc

    @_dc
    class IMUSample:                                   # type: ignore[no-redef]
        t: float
        ax: float
        ay: float
        az: float

        @property
        def vertical_deviation(self) -> float:
            return abs(self.az - 1.0)

        @property
        def magnitude(self) -> float:
            return math.sqrt(self.ax ** 2 + self.ay ** 2 + self.az ** 2)


# ---------------------------------------------------------------------------
# Device constants
# ---------------------------------------------------------------------------

I2C_SLAVE = 0x0703
I2C_FUNCS = 0x0705
I2C_RDWR = 0x0707
I2C_M_RD = 0x0001

I2C_FUNC_I2C = 0x00000001
I2C_FUNC_SMBUS_READ_BYTE_DATA = 0x00080000

DEFAULT_BUS = 3          # software I2C on GPIO23/24 — see module docstring
DEFAULT_ADDR = 0x68      # AD0 low. 0x69 if AD0 is tied high.

REG_SMPLRT_DIV = 0x19
REG_CONFIG = 0x1A
REG_GYRO_CONFIG = 0x1B
REG_ACCEL_CONFIG = 0x1C
REG_ACCEL_XOUT_H = 0x3B
REG_PWR_MGMT_1 = 0x6B
REG_WHO_AM_I = 0x75

# Accelerometer full-scale range: setting byte -> (label, LSB per g)
ACCEL_RANGES = {
    2:  (0x00, 16384.0),
    4:  (0x08, 8192.0),
    8:  (0x10, 4096.0),
    16: (0x18, 2048.0),
}

GYRO_SENSITIVITY = 131.0   # LSB per deg/s at +/-250 deg/s


class _i2c_msg(ctypes.Structure):
    """One I2C message in a combined transaction (struct i2c_msg)."""
    _fields_ = [
        ("addr", ctypes.c_uint16),
        ("flags", ctypes.c_uint16),
        ("len", ctypes.c_uint16),
        ("buf", ctypes.POINTER(ctypes.c_uint8)),
    ]


class _i2c_rdwr_data(ctypes.Structure):
    """struct i2c_rdwr_ioctl_data."""
    _fields_ = [
        ("msgs", ctypes.POINTER(_i2c_msg)),
        ("nmsgs", ctypes.c_uint32),
    ]


class IMUError(RuntimeError):
    """Raised when the inertial unit cannot be reached or does not identify."""


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

@dataclass
class IMUReading:
    """One full sample: acceleration in g, rotation in deg/s, die temp in C."""
    t: float
    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float
    temp_c: float

    def to_sample(self) -> IMUSample:
        """Reduce to the acceleration-only form the fusion module consumes."""
        return IMUSample(t=self.t, ax=self.ax, ay=self.ay, az=self.az)

    @property
    def vertical_deviation(self) -> float:
        return abs(self.az - 1.0)

    @property
    def magnitude(self) -> float:
        return math.sqrt(self.ax ** 2 + self.ay ** 2 + self.az ** 2)


class MPU6050:
    """
    Minimal MPU-6050 driver.

    The device is configured for road monitoring rather than for general
    motion capture: the digital low-pass filter is engaged to attenuate engine
    and drivetrain vibration, and the sample rate is set well above the rate at
    which a vehicle can traverse a defect so that a strike is not missed
    between samples.
    """

    def __init__(
        self,
        bus: int = DEFAULT_BUS,
        addr: int = DEFAULT_ADDR,
        accel_range_g: int = 4,
        sample_rate_hz: int = 100,
        dlpf: int = 3,
        transport: str = "auto",
    ) -> None:
        if accel_range_g not in ACCEL_RANGES:
            raise ValueError(f"accel_range_g must be one of {sorted(ACCEL_RANGES)}")

        self.bus = bus
        self.addr = addr
        self.path = f"/dev/i2c-{bus}"
        self.accel_range_g = accel_range_g
        self._lsb_per_g = ACCEL_RANGES[accel_range_g][1]
        self.sample_rate_hz = sample_rate_hz
        self._dlpf = dlpf
        self._fd: Optional[int] = None
        self.bias = (0.0, 0.0, 0.0)     # accelerometer zero offsets, in g
        self.transport = transport      # auto -> resolved on open()
        self.retries = 4                # per transaction — see _xfer
        self.retry_count = 0            # cumulative, reported by --verify

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> "MPU6050":
        if not os.path.exists(self.path):
            raise IMUError(
                f"{self.path} does not exist. The bus is not enabled. "
                f"For software I2C add 'dtoverlay=i2c-gpio,bus={self.bus},"
                f"i2c_gpio_sda=23,i2c_gpio_scl=24' to /boot/firmware/config.txt "
                f"and reboot."
            )
        try:
            self._fd = os.open(self.path, os.O_RDWR)
        except PermissionError as exc:
            raise IMUError(
                f"No permission to open {self.path}. Add the user to the i2c "
                f"group: sudo usermod -aG i2c $USER, then log out and back in."
            ) from exc

        fcntl.ioctl(self._fd, I2C_SLAVE, self.addr)

        # Resolve the transfer style. The combined I2C_RDWR transaction issues
        # a repeated start between the register write and the data read, which
        # bit-banged adapters handle far more reliably than two separate
        # transactions with a STOP in between. The simple read/write path is
        # retained as a fallback for adapters that do not advertise
        # I2C_FUNC_I2C.
        if self.transport == "auto":
            self.transport = "rdwr" if self._supports_rdwr() else "simple"

        who = self._read_byte(REG_WHO_AM_I)
        # The register reports the address with bit 0 masked out. Genuine
        # MPU-6050 parts return 0x68; several clones return 0x70, 0x72 or 0x98
        # and are otherwise register-compatible, so they are accepted.
        if who not in (0x68, 0x69, 0x70, 0x72, 0x98):
            raise IMUError(
                f"Device at 0x{self.addr:02x} on bus {self.bus} returned "
                f"WHO_AM_I=0x{who:02x}, which is not an MPU-6050 family part. "
                f"Check that the address belongs to the IMU and not another "
                f"device on the same bus."
            )
        self._who_am_i = who
        self._configure()
        return self

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "MPU6050":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- register access ---------------------------------------------------

    def _supports_rdwr(self) -> bool:
        """Ask the adapter whether it can do combined I2C transactions."""
        assert self._fd is not None
        funcs = ctypes.c_ulong()
        try:
            fcntl.ioctl(self._fd, I2C_FUNCS, funcs)
        except OSError:
            return False
        self.adapter_funcs = funcs.value
        return bool(funcs.value & I2C_FUNC_I2C)

    def _xfer(self, msgs: List[_i2c_msg]) -> None:
        """
        Run a combined transaction, retrying on transient bus failure.

        Software I2C is bit-banged by the CPU, so a transfer can be corrupted
        when the kernel preempts the driver mid-bit. Occasional retries are a
        documented mitigation rather than a defect, and the cumulative count is
        reported so that Section 4.9 can state the observed transaction failure
        rate rather than asserting the bus is clean.
        """
        assert self._fd is not None
        arr = (_i2c_msg * len(msgs))(*msgs)
        data = _i2c_rdwr_data(msgs=arr, nmsgs=len(msgs))
        last: Optional[OSError] = None
        for attempt in range(self.retries):
            try:
                fcntl.ioctl(self._fd, I2C_RDWR, data)
                return
            except OSError as exc:
                last = exc
                self.retry_count += 1
                time.sleep(0.002 * (attempt + 1))
        raise IMUError(self._explain(last))

    def _explain(self, exc: Optional[OSError]) -> str:
        errno = getattr(exc, "errno", None)
        base = (f"Transfer to 0x{self.addr:02x} on /dev/i2c-{self.bus} failed "
                f"after {self.retries} attempts ({exc}).")
        if errno == 6:      # ENXIO
            return base + (
                " The device did not acknowledge its address. If i2cdetect "
                "still sees it, the wiring is marginal — reseat SDA, SCL and "
                "ground, and prefer soldered or firmly seated jumpers over a "
                "loose breadboard contact."
            )
        if errno == 121:    # EREMOTEIO
            return base + (
                " The device acknowledged but the data transfer failed. This "
                "is usually inadequate pull-up on a bit-banged bus or wires "
                "long enough to distort the signal. Shorten the leads."
            )
        if errno == 16:     # EBUSY
            return base + (
                " The bus is held by another process or driver. Check that no "
                "other reader is running and that no overlay has claimed the "
                "address."
            )
        return base

    def _write_byte(self, reg: int, value: int) -> None:
        assert self._fd is not None
        if self.transport == "simple":
            os.write(self._fd, bytes([reg, value]))
            return
        buf = (ctypes.c_uint8 * 2)(reg, value)
        self._xfer([_i2c_msg(addr=self.addr, flags=0, len=2,
                             buf=ctypes.cast(buf,
                                             ctypes.POINTER(ctypes.c_uint8)))])

    def _read_byte(self, reg: int) -> int:
        return self._read_block(reg, 1)[0]

    def _read_block(self, reg: int, length: int) -> bytes:
        assert self._fd is not None

        if self.transport == "simple":
            os.write(self._fd, bytes([reg]))
            data = os.read(self._fd, length)
            if len(data) != length:
                raise IMUError(
                    f"Short read from register 0x{reg:02x}: expected {length} "
                    f"bytes, received {len(data)}. The bus may be unstable."
                )
            return data

        # Combined transaction: write the register pointer, repeated start,
        # then read the payload without releasing the bus.
        wbuf = (ctypes.c_uint8 * 1)(reg)
        rbuf = (ctypes.c_uint8 * length)()
        self._xfer([
            _i2c_msg(addr=self.addr, flags=0, len=1,
                     buf=ctypes.cast(wbuf, ctypes.POINTER(ctypes.c_uint8))),
            _i2c_msg(addr=self.addr, flags=I2C_M_RD, len=length,
                     buf=ctypes.cast(rbuf, ctypes.POINTER(ctypes.c_uint8))),
        ])
        return bytes(rbuf)

    def _configure(self) -> None:
        # Wake the device and clock it from the gyroscope X reference, which
        # is more stable than the internal 8 MHz oscillator.
        self._write_byte(REG_PWR_MGMT_1, 0x01)
        time.sleep(0.05)

        # Digital low-pass filter. dlpf=3 gives roughly 44 Hz accelerometer
        # bandwidth, which passes the impulse from a wheel strike while
        # attenuating engine and tyre vibration above it.
        self._write_byte(REG_CONFIG, self._dlpf & 0x07)

        # With the DLPF engaged the gyroscope output rate is 1 kHz, so the
        # divider is 1000/rate - 1.
        div = max(0, min(255, int(round(1000.0 / self.sample_rate_hz)) - 1))
        self._write_byte(REG_SMPLRT_DIV, div)
        self.sample_rate_hz = 1000.0 / (div + 1)

        self._write_byte(REG_GYRO_CONFIG, 0x00)                    # +/-250 deg/s
        self._write_byte(REG_ACCEL_CONFIG, ACCEL_RANGES[self.accel_range_g][0])
        time.sleep(0.05)

    # -- sampling ----------------------------------------------------------

    def read(self) -> IMUReading:
        """Read one sample. Acceleration in g, rotation in deg/s."""
        raw = self._read_block(REG_ACCEL_XOUT_H, 14)
        ax, ay, az, temp, gx, gy, gz = struct.unpack(">hhhhhhh", raw)

        bx, by, bz = self.bias
        return IMUReading(
            t=time.time(),
            ax=ax / self._lsb_per_g - bx,
            ay=ay / self._lsb_per_g - by,
            az=az / self._lsb_per_g - bz,
            gx=gx / GYRO_SENSITIVITY,
            gy=gy / GYRO_SENSITIVITY,
            gz=gz / GYRO_SENSITIVITY,
            temp_c=temp / 340.0 + 36.53,
        )

    def read_sample(self) -> IMUSample:
        """Read one sample in the form the fusion module consumes."""
        return self.read().to_sample()

    def stream(self, rate_hz: Optional[float] = None):
        """Yield readings at approximately the configured rate, indefinitely."""
        period = 1.0 / (rate_hz or self.sample_rate_hz)
        next_t = time.perf_counter()
        while True:
            yield self.read()
            next_t += period
            delay = next_t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.perf_counter()

    # -- calibration -------------------------------------------------------

    def calibrate(self, seconds: float = 3.0, mounted_axis: str = "z") -> tuple:
        """
        Estimate accelerometer zero offsets with the unit at rest.

        The axis pointing away from the road surface should read +1 g and the
        other two should read 0 g. Whatever they actually read at rest is
        taken as the offset. The unit must be still and on the surface it will
        be mounted against while this runs.

        Returns the offsets in g as (x, y, z).
        """
        self.bias = (0.0, 0.0, 0.0)
        xs, ys, zs = [], [], []
        end = time.time() + seconds
        while time.time() < end:
            r = self.read()
            xs.append(r.ax)
            ys.append(r.ay)
            zs.append(r.az)
            time.sleep(1.0 / self.sample_rate_hz)

        expect = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
        ex, ey, ez = expect[mounted_axis.lower()]
        self.bias = (
            statistics.fmean(xs) - ex,
            statistics.fmean(ys) - ey,
            statistics.fmean(zs) - ez,
        )
        return self.bias


# ---------------------------------------------------------------------------
# Command-line checks
# ---------------------------------------------------------------------------

def _diagnose(args) -> int:
    """
    Isolate where an inertial-unit failure lies, in order, without assuming
    any stage succeeded. Each check is reported whether it passes or fails, so
    the first failure identifies the fault instead of masking the ones after
    it.
    """
    path = f"/dev/i2c-{args.bus}"
    print(f"Diagnosing {path}, address 0x{args.addr:02x}\n")

    # 1 — device node
    print("1  bus device node")
    if not os.path.exists(path):
        print(f"   FAIL  {path} does not exist. The overlay is not loaded.")
        print(f"         Check /boot/firmware/config.txt for a line reading")
        print(f"         dtoverlay=i2c-gpio,bus={args.bus},i2c_gpio_sda=23,"
              f"i2c_gpio_scl=24")
        return 2
    print(f"   ok    {path} present")

    # 2 — permissions
    print("2  permissions")
    try:
        fd = os.open(path, os.O_RDWR)
    except PermissionError:
        print(f"   FAIL  cannot open {path}. Run: sudo usermod -aG i2c $USER")
        print(f"         then log out and back in.")
        return 2
    except OSError as exc:
        print(f"   FAIL  cannot open {path}: {exc}")
        return 2
    print(f"   ok    opened read/write")

    try:
        # 3 — adapter capability
        print("3  adapter capability")
        funcs = ctypes.c_ulong()
        try:
            fcntl.ioctl(fd, I2C_FUNCS, funcs)
            has_i2c = bool(funcs.value & I2C_FUNC_I2C)
            print(f"   ok    functionality mask 0x{funcs.value:08x}")
            print(f"         combined transactions (I2C_FUNC_I2C): "
                  f"{'yes' if has_i2c else 'no'}")
        except OSError as exc:
            has_i2c = False
            print(f"   warn  could not query functionality: {exc}")

        # 4 — address scan
        print("4  address scan")
        found = []
        errnos = set()
        for a in range(0x03, 0x78):
            try:
                fcntl.ioctl(fd, I2C_SLAVE, a)
            except OSError:
                continue
            # A zero-length write addresses the device and checks for an
            # acknowledgement without transferring data, which is what
            # i2cdetect does. Where an adapter rejects that, a one-byte read
            # answers the same question.
            for probe in ("quick", "read"):
                try:
                    if probe == "quick":
                        os.write(fd, b"")
                    else:
                        os.read(fd, 1)
                    found.append(a)
                    break
                except OSError as exc:
                    errnos.add(exc.errno)
        if found:
            print(f"   ok    responding: "
                  f"{', '.join(f'0x{a:02x}' for a in found)}")
        else:
            print(f"   FAIL  nothing responded on the bus "
                  f"(errno seen: {sorted(e for e in errnos if e)}).")
            if errnos and not (errnos & {6, 110}):
                print(f"         No address was refused the usual way "
                      f"(ENXIO), so the probe itself may be unsupported "
                      f"here.")
                print(f"         Cross-check with:  i2cdetect -y {args.bus}")
            else:
                print(f"         Power and ground are suspect before the data "
                      f"lines. Confirm the module's LED is lit.")
            return 2
        if len(found) > 8:
            print(f"   warn  {len(found)} addresses responded. A bus where "
                  f"almost everything answers is usually floating —")
            print(f"         SDA or SCL is not actually connected and the "
                  f"reads are noise.")
        if args.addr not in found:
            print(f"   FAIL  0x{args.addr:02x} is not among them. The IMU is "
                  f"not at the expected address.")
            print(f"         An MPU-6050 sits at 0x68, or 0x69 if AD0 is tied "
                  f"high. Retry with --addr for one of the addresses above.")
            return 2

        # 5 — transports
        fcntl.ioctl(fd, I2C_SLAVE, args.addr)
        print("5  register read of WHO_AM_I (0x75)")

        rdwr_ok = simple_ok = False
        if has_i2c:
            wbuf = (ctypes.c_uint8 * 1)(REG_WHO_AM_I)
            rbuf = (ctypes.c_uint8 * 1)()
            msgs = (_i2c_msg * 2)(
                _i2c_msg(addr=args.addr, flags=0, len=1,
                         buf=ctypes.cast(wbuf, ctypes.POINTER(ctypes.c_uint8))),
                _i2c_msg(addr=args.addr, flags=I2C_M_RD, len=1,
                         buf=ctypes.cast(rbuf, ctypes.POINTER(ctypes.c_uint8))),
            )
            data = _i2c_rdwr_data(msgs=msgs, nmsgs=2)
            for attempt in range(4):
                try:
                    fcntl.ioctl(fd, I2C_RDWR, data)
                    rdwr_ok = True
                    print(f"   ok    combined transaction returned "
                          f"0x{rbuf[0]:02x}"
                          f"{'' if attempt == 0 else f' (after {attempt} retries)'}")
                    break
                except OSError as exc:
                    err = exc
                    time.sleep(0.002 * (attempt + 1))
            if not rdwr_ok:
                print(f"   fail  combined transaction: {err}")

        try:
            os.write(fd, bytes([REG_WHO_AM_I]))
            got = os.read(fd, 1)
            simple_ok = len(got) == 1
            print(f"   ok    separate write/read returned 0x{got[0]:02x}")
        except OSError as exc:
            print(f"   fail  separate write/read: {exc}")

        print()
        if rdwr_ok:
            print("   Use --transport rdwr (this is the default when the "
                  "adapter supports it).")
            return 0
        if simple_ok:
            print("   Combined transactions fail but separate ones work. "
                  "Run with --transport simple.")
            return 0

        print("   Both transfer styles failed while the address itself "
              "responds to a probe.")
        print("   That pattern means the device acknowledges its address but "
              "cannot complete a data transfer — a marginal physical "
              "connection.")
        print("   In order of likelihood: reseat SDA and SCL, then ground, "
              "then shorten the leads, then try a slower bus by adding")
        print(f"   i2c_gpio_delay_us=10 to the overlay line for bus "
              f"{args.bus}.")
        return 1
    finally:
        os.close(fd)


def _probe(args) -> int:
    try:
        with MPU6050(bus=args.bus, addr=args.addr,
                     accel_range_g=args.range,
                     transport=args.transport) as imu:
            r = imu.read()
            print(f"  device        0x{args.addr:02x} on /dev/i2c-{args.bus}")
            print(f"  WHO_AM_I      0x{imu._who_am_i:02x}")
            print(f"  transport     {imu.transport}")
            print(f"  sample rate   {imu.sample_rate_hz:.1f} Hz")
            print(f"  accel range   +/-{imu.accel_range_g} g "
                  f"({ACCEL_RANGES[imu.accel_range_g][1]:.0f} LSB/g)")
            print(f"  acceleration  x={r.ax:+.3f}  y={r.ay:+.3f}  z={r.az:+.3f}  g")
            print(f"  rotation      x={r.gx:+.2f}  y={r.gy:+.2f}  z={r.gz:+.2f}  deg/s")
            print(f"  die temp      {r.temp_c:.1f} C")
            print()
            mag = r.magnitude
            if 0.85 <= mag <= 1.15:
                print(f"  PASS  resultant magnitude {mag:.3f} g is consistent "
                      f"with gravity at rest.")
                return 0
            print(f"  CHECK resultant magnitude is {mag:.3f} g. At rest this "
                  f"should be close to 1.000 g.")
            print(f"        If the unit is moving this is expected. If it is "
                  f"still, the wiring or the part is suspect.")
            return 1
    except IMUError as exc:
        print(f"  FAIL  {exc}")
        print(f"\n  Run  python3 -m edge.imu --diagnose  to isolate the stage "
              f"that is failing.")
        return 2


def _stream(args) -> int:
    with MPU6050(bus=args.bus, addr=args.addr, accel_range_g=args.range,
                 transport=args.transport) as imu:
        if args.calibrate:
            print("Calibrating. Keep the unit still.")
            bx, by, bz = imu.calibrate()
            print(f"Offsets applied: x={bx:+.4f} y={by:+.4f} z={bz:+.4f} g\n")
        print(f"{'ax':>8}{'ay':>8}{'az':>8}{'|a|':>8}{'dev':>8}   "
              f"{'gz':>8}   bar")
        try:
            for r in imu.stream(rate_hz=args.rate):
                dev = r.vertical_deviation
                bar = "#" * min(40, int(dev * 20))
                print(f"{r.ax:+8.3f}{r.ay:+8.3f}{r.az:+8.3f}"
                      f"{r.magnitude:8.3f}{dev:8.3f}{r.gz:+11.2f}   {bar}",
                      end="\r" if not args.lines else "\n", flush=True)
        except KeyboardInterrupt:
            print("\nStopped.")
    return 0


def _verify(args) -> int:
    """
    Acceptance test. Confirms the unit produces real, responsive data rather
    than merely acknowledging its address, and records the result for
    Section 4.2.

    Three phases: at rest, a settling period, then a period during which the
    operator taps or shakes the unit. A device that is wired but faulty will
    return a constant or zero reading through all three.
    """
    out = {}
    with MPU6050(bus=args.bus, addr=args.addr, accel_range_g=args.range,
                 transport=args.transport) as imu:
        print("Phase 1 of 3 — calibrating at rest. Do not touch the unit.")
        bias = imu.calibrate(seconds=3.0)
        out["bias_g"] = {"x": bias[0], "y": bias[1], "z": bias[2]}

        print("Phase 2 of 3 — recording 10 s at rest. Still do not touch it.")
        rest: List[IMUReading] = []
        end = time.time() + 10
        while time.time() < end:
            rest.append(imu.read())
            time.sleep(1.0 / imu.sample_rate_hz)

        rest_dev = [r.vertical_deviation for r in rest]
        noise = statistics.pstdev(rest_dev)
        out["rest"] = {
            "n": len(rest),
            "mean_vertical_deviation_g": statistics.fmean(rest_dev),
            "noise_floor_g": noise,
            "max_vertical_deviation_g": max(rest_dev),
            "mean_magnitude_g": statistics.fmean(r.magnitude for r in rest),
            "die_temp_c": statistics.fmean(r.temp_c for r in rest),
        }
        print(f"  noise floor {noise:.4f} g, "
              f"resting magnitude {out['rest']['mean_magnitude_g']:.3f} g")

        print("Phase 3 of 3 — recording 15 s. Tap the unit firmly several "
              "times now.")
        moved: List[IMUReading] = []
        end = time.time() + 15
        while time.time() < end:
            moved.append(imu.read())
            time.sleep(1.0 / imu.sample_rate_hz)

        moved_dev = [r.vertical_deviation for r in moved]
        peak = max(moved_dev)
        out["disturbed"] = {
            "n": len(moved),
            "peak_vertical_deviation_g": peak,
            "mean_vertical_deviation_g": statistics.fmean(moved_dev),
            "samples_above_rest_max": sum(
                1 for d in moved_dev if d > out["rest"]["max_vertical_deviation_g"]),
        }
        out["sample_rate_hz"] = imu.sample_rate_hz
        out["accel_range_g"] = imu.accel_range_g
        out["bus"] = imu.bus
        out["addr"] = f"0x{imu.addr:02x}"
        out["transport"] = imu.transport
        out["transaction_retries"] = imu.retry_count
        out["bus_type"] = "software I2C (i2c-gpio, GPIO23 SDA / GPIO24 SCL)" \
            if imu.bus != 1 else "hardware I2C (GPIO2 SDA / GPIO3 SCL)"
        out["achieved_rate_hz"] = len(moved) / 15.0

    print()
    snr = peak / noise if noise > 0 else float("inf")
    passed = noise < 0.10 and peak > max(0.15, 5 * noise)
    out["verdict"] = "pass" if passed else "review"
    out["response_ratio"] = snr

    if passed:
        print(f"  PASS  noise floor {noise:.4f} g, tap peak {peak:.3f} g, "
              f"ratio {snr:.0f}:1")
        print(f"        The unit responds to physical disturbance. Real data "
              f"is being produced.")
    else:
        print(f"  REVIEW  noise floor {noise:.4f} g, tap peak {peak:.3f} g")
        if peak <= max(0.15, 5 * noise):
            print(f"          The reading did not rise during the tap phase. "
                  f"Either the unit was not tapped, or the values are not "
                  f"coming from the accelerometer.")
        if noise >= 0.10:
            print(f"          Resting noise is high. On software I2C this can "
                  f"indicate dropped bytes; try lowering --rate.")

    print(f"  achieved rate {out['achieved_rate_hz']:.1f} Hz against "
          f"{out['sample_rate_hz']:.1f} Hz configured")

    os.makedirs(args.results_dir, exist_ok=True)
    path = os.path.join(args.results_dir, "imu_verification.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=2)
    print(f"\n  written to {path}")
    return 0 if passed else 1


def main() -> int:
    p = argparse.ArgumentParser(description="MPU-6050 inertial reader")
    p.add_argument("--bus", type=int, default=DEFAULT_BUS,
                   help=f"I2C bus number (default {DEFAULT_BUS}, "
                        f"the software bus on GPIO23/24)")
    p.add_argument("--addr", type=lambda s: int(s, 0), default=DEFAULT_ADDR,
                   help="device address (default 0x68)")
    p.add_argument("--range", type=int, default=4, choices=sorted(ACCEL_RANGES),
                   help="accelerometer full-scale range in g (default 4)")
    p.add_argument("--rate", type=float, default=None,
                   help="streaming rate in Hz")
    p.add_argument("--calibrate", action="store_true",
                   help="zero the offsets before streaming")
    p.add_argument("--lines", action="store_true",
                   help="print one line per sample instead of updating in place")
    p.add_argument("--results-dir", default="results")
    p.add_argument("--transport", default="auto",
                   choices=("auto", "rdwr", "simple"),
                   help="I2C transfer style: combined transaction (rdwr), "
                        "separate write then read (simple), or detected (auto)")

    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--probe", action="store_true")
    mode.add_argument("--stream", action="store_true")
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--diagnose", action="store_true")

    args = p.parse_args()

    if args.stream:
        return _stream(args)
    if args.verify:
        return _verify(args)
    if args.diagnose:
        return _diagnose(args)
    return _probe(args)


if __name__ == "__main__":
    raise SystemExit(main())
