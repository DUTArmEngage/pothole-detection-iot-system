#!/usr/bin/env python3
"""
checkwiring.py — one-shot wiring check for the pothole-detection prototype.

Answers a single question for each device: is it physically connected and
talking? It does not measure performance and it does not need a GNSS fix.

Self-contained: no third-party packages, and it imports nothing from the rest
of the project, so it runs from any directory.

    python3 checkwiring.py

The inertial unit and the serial port are both searched for rather than
assumed, because this build has already had to move the IMU to a software I2C
bus and may yet have to move the GNSS receiver to a spare UART.
"""

import ctypes, fcntl, glob, os, re, struct, subprocess, sys, termios, time

# ---------------------------------------------------------------- styling ---
BOLD="\033[1m"; DIM="\033[2m"; RESET="\033[0m"
GREEN="\033[32m"; RED="\033[31m"; YELLOW="\033[33m"; CYAN="\033[36m"
if not sys.stdout.isatty():
    BOLD=DIM=RESET=GREEN=RED=YELLOW=CYAN=""

def head(t): print(f"\n{BOLD}{t}{RESET}")
def ok(t):   print(f"  {GREEN}pass{RESET}  {t}")
def bad(t):  print(f"  {RED}FAIL{RESET}  {t}")
def warn(t): print(f"  {YELLOW}note{RESET}  {t}")
def info(t): print(f"        {DIM}{t}{RESET}")

results = {}

# ============================================================ inertial unit ==
I2C_SLAVE=0x0703; I2C_RDWR=0x0707; I2C_M_RD=0x0001
class _msg(ctypes.Structure):
    _fields_=[("addr",ctypes.c_uint16),("flags",ctypes.c_uint16),
              ("len",ctypes.c_uint16),("buf",ctypes.POINTER(ctypes.c_uint8))]
class _rdwr(ctypes.Structure):
    _fields_=[("msgs",ctypes.POINTER(_msg)),("nmsgs",ctypes.c_uint32)]

def i2c_read(fd, addr, reg, n):
    w=(ctypes.c_uint8*1)(reg); r=(ctypes.c_uint8*n)()
    m=(_msg*2)(_msg(addr=addr,flags=0,len=1,
                    buf=ctypes.cast(w,ctypes.POINTER(ctypes.c_uint8))),
               _msg(addr=addr,flags=I2C_M_RD,len=n,
                    buf=ctypes.cast(r,ctypes.POINTER(ctypes.c_uint8))))
    for attempt in range(4):
        try:
            fcntl.ioctl(fd, I2C_RDWR, _rdwr(msgs=m,nmsgs=2)); return bytes(r)
        except OSError:
            time.sleep(0.002*(attempt+1))
    raise OSError("no response")

def i2c_write(fd, addr, reg, val):
    b=(ctypes.c_uint8*2)(reg,val)
    m=(_msg*1)(_msg(addr=addr,flags=0,len=2,
                    buf=ctypes.cast(b,ctypes.POINTER(ctypes.c_uint8))))
    for attempt in range(4):
        try:
            fcntl.ioctl(fd, I2C_RDWR, _rdwr(msgs=m,nmsgs=1)); return
        except OSError:
            time.sleep(0.002*(attempt+1))
    raise OSError("write to register 0x%02x not acknowledged" % reg)

def check_imu():
    head("1  MPU-6050 inertial unit")
    buses=sorted(int(p.rsplit("-",1)[1]) for p in glob.glob("/dev/i2c-*"))
    if not buses:
        bad("no I2C bus exists at all")
        info("Enable one: sudo raspi-config, or add the i2c-gpio overlay.")
        results["imu"]=False; return
    info(f"buses present: {', '.join('/dev/i2c-'+str(b) for b in buses)}")

    # -- find it --------------------------------------------------------
    found=None
    for bus in buses:
        for addr in (0x68, 0x69):
            try:
                fd=os.open(f"/dev/i2c-{bus}", os.O_RDWR)
            except OSError:
                continue
            try:
                fcntl.ioctl(fd, I2C_SLAVE, addr)
                who=i2c_read(fd, addr, 0x75, 1)[0]
                if who in (0x68,0x69,0x70,0x72,0x98):
                    found=(bus, addr, who, fd); break
            except OSError:
                pass
            os.close(fd)
        if found: break

    if not found:
        bad("no MPU-6050 responded on any bus")
        info("Check VCC on the 3.3 V rail, GND shared, SDA on pin 16, "
             "SCL on pin 18.")
        results["imu"]=False; return

    bus, addr, who, fd = found
    ok(f"found on /dev/i2c-{bus} at 0x{addr:02x}, WHO_AM_I=0x{who:02x}")

    # -- read it --------------------------------------------------------
    try:
        i2c_write(fd, addr, 0x6B, 0x01); time.sleep(0.05)   # wake
        i2c_write(fd, addr, 0x1C, 0x08); time.sleep(0.05)   # +/-4 g
        mags=[]; last=None; temp=None; dropped=0
        for _ in range(20):
            try:
                raw=i2c_read(fd, addr, 0x3B, 14)
            except OSError:
                dropped+=1; continue
            ax,ay,az,t,gx,gy,gz=struct.unpack(">hhhhhhh", raw)
            ax,ay,az=[v/8192.0 for v in (ax,ay,az)]
            last=(ax,ay,az); temp=t/340.0+36.53
            mags.append((ax*ax+ay*ay+az*az)**0.5)
            time.sleep(0.01)
    except OSError as e:
        bad(f"it identifies itself but will not complete a transfer ({e})")
        info("The address is acknowledged while the data transfer fails, which")
        info("means a marginal connection rather than a wrong pin. Reseat SDA,")
        info("SCL and ground, and keep the leads short.")
        results["imu"]=False; os.close(fd); return
    finally:
        try: os.close(fd)
        except OSError: pass

    if not mags:
        bad("identified, but every sample read failed")
        results["imu"]=False; return
    if dropped:
        warn(f"{dropped} of 20 reads were dropped — software I2C is bit-banged "
             f"and loses the occasional transfer")

    mag=sum(mags)/len(mags); spread=max(mags)-min(mags)
    info(f"x={last[0]:+.3f}  y={last[1]:+.3f}  z={last[2]:+.3f} g   "
         f"die {temp:.1f} C")

    if mag < 0.02:
        bad("every axis reads zero — the device answers but is not converting")
        info("The address is acknowledged while the sensor itself produces "
             "nothing.")
        results["imu"]=False
    elif spread < 1e-6:
        warn("all 20 samples are byte-identical, which is unusually quiet")
        info("Plausible on a very stable surface, but tap the board and rerun "
             "to be sure the values move.")
        results["imu"]=True
    elif 0.70 <= mag <= 1.35:
        ok(f"resultant {mag:.3f} g, consistent with gravity at rest")
        if abs(mag-1.0) > 0.08:
            warn(f"off by {abs(mag-1.0)*100:.0f}% — calibration, not a wiring "
                 f"fault")
            info("python3 imu.py --tumble --axes z")
        results["imu"]=True
    else:
        warn(f"resultant {mag:.3f} g — expected near 1.000 at rest")
        info("If the unit was moving, rerun. If it was still, suspect the part.")
        results["imu"]=True

    if bus != 1:
        info(f"software I2C on bus {bus} — record as a build deviation in "
             f"Section 3.14.1")

# =================================================================== GNSS ====
BAUDS={4800:termios.B4800, 9600:termios.B9600, 19200:termios.B19200,
       38400:termios.B38400, 57600:termios.B57600, 115200:termios.B115200}
NMEA=re.compile(rb"^\$(G[PNLAB][A-Z]{3}),(.*)\*([0-9A-Fa-f]{2})\s*$")

def nmea_ok(line):
    m=NMEA.match(line)
    if not m: return False
    body=line[1:line.rindex(b"*")]; got=0
    for b in body: got^=b
    return got==int(m.group(3),16)

def listen(path, baud, seconds):
    try:
        fd=os.open(path, os.O_RDWR|os.O_NOCTTY)
    except OSError:
        return None
    try:
        a=termios.tcgetattr(fd); cc=list(a[6])
        cc[termios.VMIN]=0; cc[termios.VTIME]=5
        termios.tcsetattr(fd, termios.TCSANOW,
            [termios.IGNPAR,0,termios.CS8|termios.CREAD|termios.CLOCAL,0,
             BAUDS[baud],BAUDS[baud],cc])
        termios.tcflush(fd, termios.TCIFLUSH)
        raw=b""; end=time.time()+seconds
        while time.time()<end:
            try: raw+=os.read(fd,512)
            except OSError: break
        return raw
    finally:
        os.close(fd)

def check_gnss():
    head("2  NEO-6M GNSS receiver")
    cmd=""
    for p in ("/boot/firmware/cmdline.txt","/boot/cmdline.txt"):
        if os.path.exists(p):
            cmd=open(p).read(); break
    if re.search(r"console=(serial0|ttyAMA0|ttyS0)", cmd):
        bad("a login console still holds the serial port")
        info("sudo raspi-config → Interface Options → Serial Port")
        info("   login shell over serial?  No      serial port hardware?  Yes")
        results["gnss"]=False; return

    ports=[]
    for p in ["/dev/serial0"]+sorted(glob.glob("/dev/ttyAMA*"))+["/dev/ttyS0"]:
        real=os.path.realpath(p)
        if os.path.exists(p) and real not in [os.path.realpath(q) for q in ports]:
            ports.append(p)
    if not ports:
        bad("no serial device exists")
        info("Enable the port in raspi-config, or add dtoverlay=uart3 and reboot.")
        results["gnss"]=False; return
    info(f"ports present: {', '.join(ports)}")

    live=[]
    for port in ports:
        raw=listen(port, 9600, 4.0)
        if raw is None:
            info(f"{port}: cannot open (permission? add yourself to dialout)")
            continue
        if not raw:
            info(f"{port}: silent at 9600")
            continue
        good=sum(1 for l in raw.split(b"\n") if nmea_ok(l.strip()))
        live.append((port, len(raw), good, raw))
        info(f"{port}: {len(raw)} bytes, {good} valid NMEA sentences")

    best=max(live, key=lambda r: r[2]) if live else None
    if best and best[2] > 0:
        port,_n,good,raw=best
        ok(f"receiver is wired correctly and transmitting on {port}")
        fix=None; sats=0; vis=0
        for line in raw.split(b"\n"):
            line=line.strip()
            if nmea_ok(line) and line[3:6]==b"GGA":
                f=line.decode(errors="replace").split(",")
                try:
                    fix=int(f[6]) if f[6] else 0
                    sats=int(f[7]) if f[7] else 0
                except (ValueError, IndexError):
                    pass
            elif nmea_ok(line) and line[3:6]==b"GSV":
                f=line.decode(errors="replace").split(",")
                try: vis=int(f[3]) if f[3] else vis
                except (ValueError, IndexError): pass
        if fix:
            ok(f"it also has a position fix, {sats} satellites")
        else:
            warn(f"no position fix yet — {vis} satellites visible")
            info("Expected indoors. The WIRING test above has already passed.")
            info("For a fix it needs sky; a cold start takes 30 s to a few minutes.")
        results["gnss"]=True
        if port!="/dev/serial0":
            info(f"running on {port} — record the UART change in the build notes")
        return

    if live:
        warn("bytes are arriving but none are valid NMEA — likely the wrong baud")
        for baud in (38400,4800,19200,57600,115200):
            raw=listen(live[0][0], baud, 2.5) or b""
            good=sum(1 for l in raw.split(b"\n") if nmea_ok(l.strip()))
            if good:
                ok(f"it is running at {baud} baud, not 9600")
                info(f"python3 gps.py --diagnose --baud {baud}")
                results["gnss"]=True; return
        bad("no baud rate produced valid sentences")
        results["gnss"]=False; return

    bad("every serial port was silent")
    info("1  TX and RX must cross — module TX to Pi pin 10, not pin 8")
    info("2  is the module's LED lit? no light means no power")
    info("3  is ground shared between the module and the Pi?")
    info("4  test the Pi itself:  python3 gps.py --loopback")
    results["gnss"]=False

# ================================================================= camera ====
def check_camera():
    head("3  Camera")
    nodes=sorted(glob.glob("/dev/video*"))
    codec={10,11,12,13,14,15,18,19,20,21,22,23,31}
    sensors=[n for n in nodes
             if int(re.sub(r"\D","",n) or -1) not in codec]
    if nodes:
        info(f"video nodes: {', '.join(nodes[:6])}"
             + (" ..." if len(nodes)>6 else ""))
    if not sensors:
        info("those are the Pi's hardware video codec, not a camera sensor")
    for cmd in (["rpicam-hello","--list-cameras"],
                ["libcamera-hello","--list-cameras"]):
        try:
            out=subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        text=(out.stdout or "")+(out.stderr or "")
        if "Available cameras" in text and ":" in text:
            names=[l.strip() for l in text.splitlines()
                   if re.match(r"^\s*\d+\s*:", l)]
            if names:
                ok(f"detected by {cmd[0]}")
                for n in names[:3]: info(n)
                results["camera"]=True; return
        bad(f"{cmd[0]} reports no camera attached")
        info("If a camera IS connected: power down, reseat the CSI ribbon at")
        info("both ends — contacts toward the board, blue tab toward the jack.")
        info("If no camera is connected yet, this result is simply expected.")
        results["camera"]=False; return
    warn("no camera tool installed, so only the device node could be checked")
    info("sudo apt install -y rpicam-apps   then rerun")
    results["camera"]=None

# =================================================================== main ====
def main():
    print(f"{BOLD}Prototype wiring check{RESET}")
    print(f"{DIM}Each device is searched for rather than assumed. "
          f"No GNSS fix is required.{RESET}")
    try:
        check_imu()
    except Exception as e:
        bad(f"inertial check crashed: {e}"); results["imu"]=False
    try:
        check_gnss()
    except Exception as e:
        bad(f"GNSS check crashed: {e}"); results["gnss"]=False
    try:
        check_camera()
    except Exception as e:
        bad(f"camera check crashed: {e}"); results["camera"]=False

    head("Summary")
    label={"imu":"MPU-6050 inertial unit","gnss":"NEO-6M GNSS receiver",
           "camera":"Camera"}
    for k in ("imu","gnss","camera"):
        v=results.get(k)
        mark=(f"{GREEN}connected{RESET}" if v is True else
              f"{YELLOW}unverified{RESET}" if v is None else
              f"{RED}not connected{RESET}")
        print(f"  {label[k]:<26} {mark}")
    bad_ones=[k for k,v in results.items() if v is False]
    print()
    if not bad_ones:
        print(f"  {GREEN}All sensors are wired and responding.{RESET}")
        print(f"  {DIM}Next: python3 imu.py --verify   and   "
              f"python3 gps.py --verify{RESET}")
        return 0
    print(f"  {len(bad_ones)} device(s) need attention. Work through the notes "
          f"above, top down.")
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
