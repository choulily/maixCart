"""End-to-end test: input latency + gamepad support for tools/remote_gui.py.

* latency: a key press must send 'move' immediately (no tick wait), a tap must
  stop quickly, and a held key must keep repeating (~DRIVE_TICK_MS).
* gamepad: a virtual Xbox-style pad is created through /dev/uinput (no hardware
  needed), then stick / button / D-pad / trigger events are fed through the
  real reader thread and the commands the GUI sends are checked.

Run:  python3 tools/test_gui_pad.py   (needs a display and a writable /dev/uinput)
"""
import fcntl
import os
import struct
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import remote_gui as rg                                            # noqa: E402

fails = []


def check(cond, what):
    print(("  OK   " if cond else "  FAIL ") + what)
    if not cond:
        fails.append(what)


# ------------------------------------------------- virtual gamepad ----
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_ABSBIT = 0x40045567
EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
ABS_CNT = 0x40
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05
ABS_HAT0X, ABS_HAT0Y = 0x10, 0x11
BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST = 0x130, 0x131, 0x133, 0x134
BTN_TL, BTN_TR, BTN_SELECT, BTN_START = 0x136, 0x137, 0x13a, 0x13b
BUS_USB = 0x03


class VirtualPad:
    """Xbox-style pad (js numbering: A=0 B=1 X=2 Y=3 LB=4 RB=5 Back=6 Start=7,
    LX=0 LY=1 LT=2 RX=3 RY=4 RT=5 hatX=6 hatY=7)."""

    def __init__(self, name="MaixCart Virtual Gamepad"):
        dev = bytearray(struct.pack("<80sHHHHI", name.encode(), BUS_USB,
                                    0x1234, 0x5678, 1, 0))
        dev += b"\x00" * (4 * ABS_CNT * 4)      # absmax/min/fuzz/flat tables
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        for ev in (EV_KEY, EV_ABS):
            fcntl.ioctl(self.fd, UI_SET_EVBIT, ev)
        for b in (BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST,
                  BTN_TL, BTN_TR, BTN_SELECT, BTN_START):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, b)
        for a, lo, hi in ((ABS_X, -32767, 32767), (ABS_Y, -32767, 32767),
                          (ABS_RX, -32767, 32767), (ABS_RY, -32767, 32767),
                          (ABS_Z, 0, 255), (ABS_RZ, 0, 255),
                          (ABS_HAT0X, -1, 1), (ABS_HAT0Y, -1, 1)):
            fcntl.ioctl(self.fd, UI_SET_ABSBIT, a)
            struct.pack_into("<i", dev, 92 + a * 4, hi)            # absmax
            struct.pack_into("<i", dev, 92 + 256 + a * 4, lo)      # absmin
        os.write(self.fd, bytes(dev))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)

    def emit(self, ev_type, code, value):
        os.write(self.fd, struct.pack("<qqHHi", 0, 0, ev_type, code, value))
        os.write(self.fd, struct.pack("<qqHHi", 0, 0, EV_SYN, 0, 0))

    def axis(self, code, value):
        self.emit(EV_ABS, code, value)

    def button(self, code, value):
        self.emit(EV_KEY, code, value)

    def close(self):
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except Exception:
            pass
        os.close(self.fd)


print("creating the virtual gamepad ...")
try:
    pad = VirtualPad()
except OSError as e:
    print("SKIP: cannot create a virtual pad on /dev/uinput (%s)" % e)
    sys.exit(0)
t0 = time.time()
while not os.path.exists("/dev/input/js0") and time.time() - t0 < 3:
    time.sleep(0.05)
if not os.path.exists("/dev/input/js0"):
    print("SKIP: no /dev/input/js0 appeared - is the joydev module loaded?")
    pad.close()
    sys.exit(0)
print("  .. /dev/input/js0 exists")
import stat
mode = os.stat("/dev/input/js0").st_mode
print("  .. permissions %s (can this user open it? %s)" % (
    stat.filemode(mode), os.access("/dev/input/js0", os.R_OK)))

# ------------------------------------------------------------- GUI ----
root = tk.Tk()
app = rg.App(root, {"port": 6152, "log_keys": False})
sent = []
app.link.send = lambda cmd: sent.append((time.time(), cmd))
type(app.link).connected = property(lambda self: True)


def settle(seconds):
    t0 = time.time()
    while time.time() - t0 < seconds:
        root.update()
        time.sleep(0.005)


def press(k):
    root.event_generate("<KeyPress>", keysym=k, when="now")


def release(k):
    root.event_generate("<KeyRelease>", keysym=k, when="now")


def since(t, pred):
    """First (dt, cmd) matching pred after time t, or (None, None)."""
    for ts, cmd in sent:
        if ts >= t and pred(cmd):
            return ts - t, cmd
    return None, None


print()
print("--- latency: key press ---")
sent[:] = []
t0 = time.time()
press("w")
root.update()
dt, cmd = since(t0, lambda c: c.startswith("move f"))
check(cmd is not None, "press 'w' sends 'move f' immediately (%r)" % cmd)
check(dt is not None and dt < 0.03, "  ... within 30 ms (%.0f ms)" % ((dt or 9) * 1000))
release("w")
settle(0.4)
check(sent[-1][1] == "stop", "release ends with 'stop'")

print()
print("--- latency: tapped key (press+release fast) ---")
sent[:] = []
press("s")
root.update()
settle(0.05)
release("s")
t_rel = time.time()
settle(0.4)
dt, cmd = since(t_rel, lambda c: c == "stop")
check(cmd is not None, "tap stop sent")
check(dt is not None and dt < 0.20, "  ... within 200 ms of release (%.0f ms)" % ((dt or 9) * 1000))

print()
print("--- latency: held key repeat rate ---")
sent[:] = []
press("w")
settle(0.6)
release("w")
settle(0.3)
moves = [c for _, c in sent if c.startswith("move f")]
check(len(moves) >= 8, "held key re-sends ~every %d ms (%d sends in 0.6 s)"
      % (rg.DRIVE_TICK_MS, len(moves)))
check(sent[-1][1] == "stop", "held key stops after release")

print()
print("--- gamepad discovery ---")
t0 = time.time()
while app.pad.path is None and time.time() - t0 < 5:
    settle(0.1)
check(app.pad.path == "/dev/input/js0", "reader opened %r" % app.pad.path)
check(app.pad.name == "MaixCart Virtual Gamepad", "device name %r" % app.pad.name)
check("Virtual Gamepad" in app.pad.note, "status note %r" % app.pad.note)

print()
print("--- gamepad: left stick drives ---")
sent[:] = []
pad.axis(ABS_Y, -32767)                     # stick up = forward
settle(0.25)
pad.axis(ABS_Y, 0)
settle(0.3)
check(any(c == "move f 55" for _, c in sent), "stick up -> 'move f' (%r)" % sent[:3])
check(sent[-1][1] == "stop", "stick back to center -> 'stop'")

sent[:] = []
pad.axis(ABS_X, 32767)                      # stick right = strafe right
settle(0.2)
pad.axis(ABS_X, 0)
settle(0.25)
check(any(c == "move r 55" for _, c in sent), "stick right -> 'move r'")

print()
print("--- gamepad: right stick spins ---")
sent[:] = []
pad.axis(ABS_RX, 32767)
settle(0.2)
pad.axis(ABS_RX, 0)
settle(0.25)
check(any(c == "move tr 55" for _, c in sent), "right stick -> 'move tr'")

print()
print("--- gamepad: face buttons, start, and the little arm ---")
sent[:] = []
pad.button(BTN_SOUTH, 1)
settle(0.15)
pad.button(BTN_SOUTH, 0)
settle(0.1)
check(any(c == "grab" for _, c in sent), "A -> 'grab'")
sent[:] = []
pad.button(BTN_NORTH, 1)                    # X = home
settle(0.15)
pad.button(BTN_NORTH, 0)
check(any(c == "home" for _, c in sent), "X -> 'home'")
sent[:] = []
pad.button(BTN_START, 1)
settle(0.15)
pad.button(BTN_START, 0)
check(any(c == "estop" for _, c in sent), "Start -> 'estop'")
app._resume()
settle(0.1)
sent[:] = []
pad.button(BTN_WEST, 1)                     # Y = stop wheels
settle(0.15)
pad.button(BTN_WEST, 0)
check(any(c == "stop" for _, c in sent), "Y -> 'stop'")
sent[:] = []
pad.button(BTN_TL, 1)                       # LB = little arm up, repeats
settle(0.6)
pad.button(BTN_TL, 0)
settle(0.1)
n = len([c for _, c in sent if c.startswith("nudge little_arm")])
check(n >= 2, "LB held -> repeated little-arm nudges (%d)" % n)

print()
print("--- gamepad: D-pad arm, triggers, unplug ---")
sent[:] = []
pad.axis(ABS_HAT0Y, -1)                     # D-pad up = big arm up
settle(0.15)
pad.axis(ABS_HAT0Y, 0)
settle(0.1)
check(any(c.startswith("nudge big_arm") for _, c in sent), "D-pad up -> big arm nudge")
sent[:] = []
pad.axis(ABS_HAT0X, 1)                      # D-pad right = rotate right
settle(0.15)
pad.axis(ABS_HAT0X, 0)
settle(0.1)
check(any(c.startswith("nudge rotation") for _, c in sent), "D-pad right -> rotation nudge")
sent[:] = []
before = int(float(app.speed.get()))
pad.axis(ABS_RZ, 255)                       # right trigger = speed up
settle(0.4)
pad.axis(ABS_RZ, 0)
settle(0.2)
check(int(float(app.speed.get())) > before, "RT -> speed %d -> %d"
      % (before, int(float(app.speed.get()))))

pad.close()
t0 = time.time()
while app.pad.path is not None and time.time() - t0 < 6:
    settle(0.1)
settle(0.5)                                   # let the reader update its note
print("  .. note after unplug: %r" % app.pad.note)
check("none" in app.pad.note or "no gamepad" in app.pad.note,
      "unplugged pad falls back to scanning (%r)" % app.pad.note)
check(app.pad.path is None, "no stale device path")

print()
print("FAILED: %d" % len(fails))
for f in fails:
    print("  - " + f)
app.pad.close()
app.link.shutdown()
root.destroy()
sys.exit(1 if fails else 0)
