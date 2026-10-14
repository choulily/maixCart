"""End-to-end test: input latency + gamepad support + steering mixes.

* latency: a key press must send 'move' immediately (no tick wait), a tap must
  stop quickly, and a held key must keep repeating (~DRIVE_TICK_MS).
* gamepad: a virtual pad with a *Stadia-like* evdev layout (right stick on
  ABS_Z/RZ, triggers on ABS_GAS/BRAKE, D-pad hat) is created through
  /dev/uinput - no hardware needed.  The reader must pick the right axes from
  the device's declared ranges, and the commands the GUI sends are checked.
* steering: single inputs send plain 'move' (with the analog speed), blends
  send 'mix fwd strafe spin' so the cart steers while driving.

Run:  python3 tools/test_gui_pad.py   (needs a display and a writable /dev/uinput)
"""
import fcntl
import glob
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
ABS_X, ABS_Y, ABS_Z, ABS_RZ = 0x00, 0x01, 0x02, 0x05
ABS_GAS, ABS_BRAKE = 0x09, 0x0a
ABS_HAT0X, ABS_HAT0Y = 0x10, 0x11
BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST = 0x130, 0x131, 0x133, 0x134
BTN_TL, BTN_TR, BTN_SELECT, BTN_START = 0x136, 0x137, 0x13a, 0x13b
BUS_USB = 0x03
NAME = "MaixCart Virtual Gamepad"


class VirtualPad:
    """Stadia-like pad: left stick ABS_X/Y, right stick ABS_Z/RZ (1..255,
    centred at 128), triggers ABS_GAS (RT) / ABS_BRAKE (LT) 0..255, D-pad on
    the ABS_HAT0 axes, Xbox-style buttons."""

    def __init__(self, name=NAME):
        dev = bytearray(struct.pack("<80sHHHHI", name.encode(), BUS_USB,
                                    0x1234, 0x5678, 1, 0))
        dev += b"\x00" * (4 * ABS_CNT * 4)      # absmax/min/fuzz/flat tables
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        for ev in (EV_KEY, EV_ABS):
            fcntl.ioctl(self.fd, UI_SET_EVBIT, ev)
        for b in (BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST,
                  BTN_TL, BTN_TR, BTN_SELECT, BTN_START):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, b)
        for a, lo, hi in ((ABS_X, 1, 255), (ABS_Y, 1, 255), (ABS_Z, 1, 255),
                          (ABS_RZ, 1, 255), (ABS_GAS, 0, 255), (ABS_BRAKE, 0, 255),
                          (ABS_HAT0X, -1, 1), (ABS_HAT0Y, -1, 1)):
            fcntl.ioctl(self.fd, UI_SET_ABSBIT, a)
            struct.pack_into("<i", dev, 92 + a * 4, hi)            # absmax
            struct.pack_into("<i", dev, 92 + 256 + a * 4, lo)      # absmin
        os.write(self.fd, bytes(dev))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        for a in (ABS_X, ABS_Y, ABS_Z, ABS_RZ):        # report a real rest state
            self.emit(EV_ABS, a, 128)
        for a in (ABS_GAS, ABS_BRAKE, ABS_HAT0X, ABS_HAT0Y):
            self.emit(EV_ABS, a, 0)

    def emit(self, ev_type, code, value):
        os.write(self.fd, struct.pack("<qqHHi", 0, 0, ev_type, code, value))
        os.write(self.fd, struct.pack("<qqHHi", 0, 0, EV_SYN, 0, 0))

    def stick(self, code, value):
        """value -1..1 -> the axis' 1..255 range (0 stays centred)."""
        self.emit(EV_ABS, code, 128 + int(round(max(-1.0, min(1.0, value)) * 127)))

    def trigger(self, code, value):
        self.emit(EV_ABS, code, int(round(max(0.0, min(1.0, value)) * 255)))

    def hat(self, code, value):
        self.emit(EV_ABS, code, value)

    def button(self, code, value):
        self.emit(EV_KEY, code, value)

    def close(self):
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except Exception:
            pass
        os.close(self.fd)


def event_path_of(name):
    """The /dev/input/eventN node of the (virtual) pad called `name`."""
    for path in sorted(glob.glob("/sys/class/input/event*/device/name")):
        try:
            with open(path) as f:
                if f.read().strip() == name:
                    return "/dev/input/" + path.split("/")[4]
        except OSError:
            continue
    return None


print("creating the virtual gamepad ...")
try:
    pad = VirtualPad()
except OSError as e:
    print("SKIP: cannot create a virtual pad on /dev/uinput (%s)" % e)
    sys.exit(0)
path = None
t0 = time.time()
while path is None and time.time() - t0 < 3:
    path = event_path_of(NAME)
    time.sleep(0.05)
if path is None:
    print("SKIP: the virtual pad did not show up in /dev/input")
    pad.close()
    sys.exit(0)
print("  .. virtual pad at %s" % path)

# ------------------------------------------------------------- GUI ----
root = tk.Tk()
app = rg.App(root, {"port": 6152, "log_keys": False, "pad": path})
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
    for ts, cmd in sent:
        if ts >= t and pred(cmd):
            return ts - t, cmd
    return None, None


def log_text():
    return app.logbox.get("1.0", "end")


print()
print("--- latency: key press / tap / hold ---")
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

sent[:] = []
press("s")
root.update()
settle(0.05)
release("s")
t_rel = time.time()
settle(0.4)
dt, cmd = since(t_rel, lambda c: c == "stop")
check(cmd is not None and dt < 0.20, "tapped key stops within 200 ms (%.0f ms)"
      % ((dt or 9) * 1000))

sent[:] = []
t0 = time.time()
press("w")
settle(0.6)
release("w")
settle(0.3)
moves = [c for ts, c in sent if c.startswith("move f")]
check(len(moves) >= 3 and sent[-1][0] - t0 > 0.3,
      "held key keeps driving (%d sends in 0.6 s, last at +%.2fs)"
      % (len(moves), sent[-1][0] - t0))

print()
print("--- steering mixes from the keyboard ---")
sent[:] = []
press("w")
settle(0.15)
press("a")
settle(0.15)
check(any(c == "mix 100 0 -100 55" for _, c in sent),
      "W+A steers left while driving (%r)" % [c for _, c in sent][-3:])
release("a")
settle(0.25)
check(any(c == "move f 55" for _, c in sent),
      "releasing A falls back to plain 'move f'")
release("w")
settle(0.25)
sent[:] = []
press("a")
settle(0.25)
check(any(c == "move l 55" for _, c in sent),
      "A alone (no forward vector) moves sideways")
release("a")
settle(0.3)

print()
print("--- gamepad discovery and axis layout ---")
t0 = time.time()
while app.pad.layout is None and time.time() - t0 < 4:
    settle(0.1)
lay = app.pad.layout or {}
check(app.pad.name == NAME, "reader opened the virtual pad (%r)" % app.pad.name)
check(app.pad.path == path, "device path %r" % app.pad.path)
check(lay.get("lx") == ABS_X and lay.get("ly") == ABS_Y, "left stick = ABS_X/Y (%s)" % lay)
check(lay.get("rstr") == ABS_Z and lay.get("rspin") == ABS_RZ,
      "right stick = ABS_Z/ABS_RZ (%s)" % lay)
check(lay.get("rt") == ABS_GAS and lay.get("lt") == ABS_BRAKE,
      "triggers = ABS_GAS/BRAKE (%s)" % lay)
check("rstr=ABS_Z" in log_text() and "RT" in log_text(),
      "layout logged for the user")

print()
print("--- gamepad: sticks drive and steer ---")
sent[:] = []
pad.stick(ABS_Y, -1.0)                      # full forward
settle(0.25)
pad.stick(ABS_Y, 0.0)
settle(0.3)
check(any(c == "move f 55" for _, c in sent), "left stick up -> 'move f' (%r)" % sent[:3])
check(sent[-1][1] == "stop", "stick back to centre -> 'stop'")

sent[:] = []
pad.stick(ABS_X, 1.0)                       # full right
settle(0.2)
pad.stick(ABS_X, 0.0)
settle(0.25)
check(any(c == "move r 55" for _, c in sent), "left stick right -> 'move r'")

sent[:] = []
pad.stick(ABS_Y, -0.6)                      # analog: 60 % forward
settle(0.2)
check(any(c == "move f 33" for _, c in sent),
      "60 %% stick -> analog 'move f 33' (%r)" % sent[:2])
pad.stick(ABS_Y, 0.0)
settle(0.25)

sent[:] = []
pad.stick(ABS_Y, -1.0)
pad.stick(ABS_X, 1.0)
settle(0.25)
check(any(c == "mix 100 0 100 55" for _, c in sent),
      "stick pushed diagonally steers right while driving (%r)" % sent[:2])
pad.stick(ABS_Y, 0.0)
pad.stick(ABS_X, 0.0)
settle(0.3)

sent[:] = []
pad.stick(ABS_Y, -1.0)
pad.stick(ABS_Z, 1.0)                       # forward + right stick sideways = crab
settle(0.25)
check(any(c == "mix 100 100 0 55" for _, c in sent),
      "right stick adds sideways while driving (%r)" % sent[:2])
pad.stick(ABS_Y, 0.0)
pad.stick(ABS_Z, 0.0)
settle(0.3)

sent[:] = []
pad.stick(ABS_Z, 1.0)                       # right stick sideways on its own
settle(0.2)
check(any(c == "move r 55" for _, c in sent),
      "right stick X alone -> sideways (%r)" % sent[:2])
pad.stick(ABS_Z, 0.0)
settle(0.25)

sent[:] = []
pad.stick(ABS_RZ, 1.0)                      # right stick Y = spin in place
settle(0.2)
check(any(c == "move tr 55" for _, c in sent),
      "right stick Y alone -> spin in place (%r)" % sent[:2])
pad.stick(ABS_RZ, 0.0)
settle(0.25)

print()
print("--- gamepad: buttons, D-pad, triggers ---")
sent[:] = []
pad.button(BTN_SOUTH, 1)
settle(0.15)
pad.button(BTN_SOUTH, 0)
check(any(c == "grab" for _, c in sent), "A -> 'grab'")
sent[:] = []
pad.button(BTN_NORTH, 1)
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
pad.button(BTN_WEST, 1)
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

sent[:] = []
pad.hat(ABS_HAT0Y, -1)                      # D-pad up = big arm up
settle(0.15)
pad.hat(ABS_HAT0Y, 0)
settle(0.1)
check(any(c.startswith("nudge big_arm") for _, c in sent), "D-pad up -> big arm nudge")
sent[:] = []
pad.hat(ABS_HAT0X, 1)                       # D-pad right = rotate right
settle(0.15)
pad.hat(ABS_HAT0X, 0)
settle(0.1)
check(any(c.startswith("nudge rotation") for _, c in sent), "D-pad right -> rotation nudge")

sent[:] = []
before = int(float(app.speed.get()))
pad.trigger(ABS_GAS, 1.0)                   # RT = speed up
settle(0.4)
pad.trigger(ABS_GAS, 0.0)
settle(0.2)
after_up = int(float(app.speed.get()))
check(after_up > before, "RT -> speed %d -> %d" % (before, after_up))
sent[:] = []
pad.trigger(ABS_BRAKE, 1.0)                 # LT = speed down
settle(0.4)
pad.trigger(ABS_BRAKE, 0.0)
settle(0.2)
check(int(float(app.speed.get())) < after_up,
      "LT -> speed %d -> %d" % (after_up, int(float(app.speed.get()))))

print()
print("--- unplug ---")
pad.close()
t0 = time.time()
while app.pad.path is not None and time.time() - t0 < 6:
    settle(0.1)
settle(0.5)
print("  .. note after unplug: %r" % app.pad.note)
check("none" in app.pad.note or "no gamepad" in app.pad.note,
      "unplugged pad falls back to scanning")
check(app.pad.layout is None, "no stale axis layout")

print()
print("FAILED: %d" % len(fails))
for f in fails:
    print("  - " + f)
app.pad.close()
app.link.shutdown()
root.destroy()
sys.exit(1 if fails else 0)
