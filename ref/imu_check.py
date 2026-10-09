import fcntl, os, struct, time, math, statistics
BUS, ADDR, LSB = 3, 0x68, 8192.0          # bus 3, 0x68, +/-4g
fd = os.open(f"/dev/i2c-{BUS}", os.O_RDWR)
fcntl.ioctl(fd, 0x0703, ADDR)
def wr(r, v): os.write(fd, bytes([r, v]))
def rd(r, n):
    os.write(fd, bytes([r])); return os.read(fd, n)
who = rd(0x75, 1)[0]
wr(0x6B, 0x01); time.sleep(0.05)          # wake
wr(0x1A, 0x03)                            # low-pass filter ~44 Hz
wr(0x19, 9)                               # 100 Hz
wr(0x1B, 0x00); wr(0x1C, 0x08); time.sleep(0.05)
def read():
    ax, ay, az, t, gx, gy, gz = struct.unpack(">hhhhhhh", rd(0x3B, 14))
    return ax/LSB, ay/LSB, az/LSB, t/340.0+36.53
print(f"WHO_AM_I = 0x{who:02x}")
x, y, z, tc = read()
print(f"rest:  x={x:+.3f}  y={y:+.3f}  z={z:+.3f} g   |a|={math.sqrt(x*x+y*y+z*z):.3f} g   {tc:.1f} C")
print("\nTap or tilt the module for 10 seconds...\n")
devs = []
end = time.time() + 10
while time.time() < end:
    x, y, z, _ = read()
    d = abs(z - 1.0); devs.append(d)
    print(f"  x={x:+.3f} y={y:+.3f} z={z:+.3f}  dev={d:5.3f}  {'#'*min(40,int(d*20))}   ", end="\r", flush=True)
    time.sleep(0.05)
print(f"\n\npeak deviation {max(devs):.3f} g,  resting noise {statistics.pstdev(devs[:20]):.4f} g")
print("PASS — the unit responds to movement." if max(devs) > 0.15 else "FAIL — reading did not change. Wired but not converting.")
