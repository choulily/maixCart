#!/usr/bin/env python3
"""Maixduino cart remote control - Tk GUI + TCP server.

Run this on the laptop (the K210 socket cannot listen, so this side is the
server), then start the bot on the cart - either with the "Upload & run on
cart" button here, or with  python3 tools/upload_bot.py  from a terminal.

Keyboard (the window must have focus):
    W / S  or  Up / Down        forward / backward
    A / D  or  Left / Right     strafe left / right
    Q / E                       spin left / right
    Space                       stop the wheels
    Esc                         E-STOP (latched - press again to resume)
    R / F                       big arm up / down
    T / G                       little arm up / down
    Y / H                       arm rotation left / right
    C / V                       clamp: grab / release
    1..8                        same as above (1 big arm down, 2 up,
                                3 little down, 4 up, 5 rotate left,
                                6 rotate right, 7 release, 8 grab)
    P                           home pose (all servos to SERVO_HOME)
    + / -                       drive speed        [ / ]  servo step size

Gamepad (wired through the OS, USB or Bluetooth - see the Gamepad class below):
    left stick drive, right stick spin, A/B clamp, X home, Y stop,
    Start E-STOP/resume, LB/RB little arm, D-pad big arm + rotation,
    LT/RT speed.  Works even when the window is not focused.
"""
import errno
import fcntl
import glob
import json
import os
import queue
import select
import socket
import struct
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import upload_bot
except Exception as _e:                                  # pyserial missing?
    upload_bot = None
    _UPLOAD_IMPORT_ERROR = repr(_e)
else:
    _UPLOAD_IMPORT_ERROR = None

SETTINGS = os.path.join(HERE, ".remote_gui.json")

# ---------------------------------------------------------------- protocol ---
TELEMETRY_FIELDS = ("ms", "dist", "c0", "c1", "c2", "c3", "c4", "onmask",
                    "big_arm", "little_arm", "rotation", "clamp", "flags")
SERVO_NAMES = ("big_arm", "little_arm", "rotation", "clamp")
MOVE_KEYS = {"w": "f", "up": "f", "s": "b", "down": "b",
             "a": "l", "left": "l", "d": "r", "right": "r",
             "q": "tl", "e": "tr"}
MOVE_LABEL = {"f": "forward", "b": "backward", "l": "strafe left", "r": "strafe right",
              "tl": "spin left", "tr": "spin right"}
# movement code -> role and sign in the drive vector (see _move_vector):
#   x = forward/backward, h = steer while driving / strafe when not,
#   w = spin (always)
MOVE_ROLES = {"f": ("x", +1), "b": ("x", -1),
              "l": ("h", -1), "r": ("h", +1),
              "tl": ("w", -1), "tr": ("w", +1)}
SERVO_KEYS = {
    "r": ("big_arm", +1), "f": ("big_arm", -1),
    "t": ("little_arm", +1), "g": ("little_arm", -1),
    "y": ("rotation", +1), "h": ("rotation", -1),
    "1": ("big_arm", -1), "2": ("big_arm", +1),
    "3": ("little_arm", -1), "4": ("little_arm", +1),
    "5": ("rotation", -1), "6": ("rotation", +1),
    "7": ("clamp", "release"), "8": ("clamp", "grab"),
    "c": ("clamp", "grab"), "v": ("clamp", "release"),
}
STALE_TELEMETRY_S = 1.5          # after this long without telemetry: "silent"
DRIVE_TICK_MS = 60               # re-send the held input this often (the bot's
                                 # watchdog needs a 'move' every 600 ms)
DRIVE_KEEPALIVE_S = 0.15         # ... but only re-send an *unchanged* command
                                 # this often: the ESP32/WiFi link is the slow
                                 # part, no need for 16 identical packets a second
REPEAT_GAP_S = 0.15              # a release + auto-repeat press gap is shorter than this
RELEASE_GAP_S = 0.10             # ... and for a key that never auto-repeated
MAX_KEY_HOLD_S = 10.0            # safety: forget a key that sent no events for this long
KEY_CHAR_ALIASES = {" ": "space"}

# ------------------------------------------------------------- gamepad ---
# A gamepad connected to THIS laptop (USB or Bluetooth - the OS pairs or plugs
# it in, the GUI just reads it) drives the cart through the same path as the
# keyboard and the mouse pad.  The cart's ESP32 cannot host a controller: it
# runs the WiFi/ADC firmware, and Bluetooth HID would need new firmware on it.
#
# The reader uses the *evdev* interface (/dev/input/eventN), where every pad
# reports standard codes (BTN_SOUTH, ABS_X, ABS_HAT0Y, ...) *and* each axis'
# range - so the same mapping works for Xbox/DualShock/Stadia/8BitDo pads.
# (The legacy /dev/input/jsN numbering differs per pad/driver and hides the
# ranges: that is what made an earlier version misread a Stadia controller.)
# Tick "log keys" to see the codes a pad sends; remap with the tables below.
PAD_DEADZONE = 0.35              # stick travel that starts moving
PAD_RELEASE = 0.22               # ... and that stops again (hysteresis)
PAD_HAT = 0.5                    # hat (D-pad) deflection that counts as pressed
PAD_TRIGGER = 0.5                # trigger travel that counts as pressed
PAD_REPEAT_S = 0.25              # held arm buttons / D-pad repeat this often

PAD_EV_KEY, PAD_EV_ABS = 1, 3
PAD_ABS_X, PAD_ABS_Y = 0x00, 0x01          # left stick (X, Y)
PAD_ABS_Z, PAD_ABS_RZ = 0x02, 0x05         # right stick X / Y on some pads
PAD_ABS_RX = 0x03                          # right stick X on most pads
PAD_ABS_GAS, PAD_ABS_BRAKE = 0x09, 0x0a    # RT / LT (Stadia & friends)
PAD_ABS_HAT0X, PAD_ABS_HAT0Y = 0x10, 0x11  # D-pad
PAD_ABS_RY = 0x04                          # right stick Y on most pads
PAD_AXIS_PAIR = {PAD_ABS_Z: PAD_ABS_RZ, PAD_ABS_RZ: PAD_ABS_Z,
                 PAD_ABS_RX: PAD_ABS_RY, PAD_ABS_RY: PAD_ABS_RX}  # horiz <-> vert
PAD_AXIS_NAMES = {PAD_ABS_X: "ABS_X", PAD_ABS_Y: "ABS_Y", PAD_ABS_Z: "ABS_Z",
                  PAD_ABS_RX: "ABS_RX", 0x04: "ABS_RY", PAD_ABS_RZ: "ABS_RZ",
                  PAD_ABS_GAS: "ABS_GAS(RT)", PAD_ABS_BRAKE: "ABS_BRAKE(LT)",
                  PAD_ABS_HAT0X: "ABS_HAT0X", PAD_ABS_HAT0Y: "ABS_HAT0Y"}
PAD_BTN_NAMES = {0x130: "BTN_SOUTH(A)", 0x131: "BTN_EAST(B)", 0x133: "BTN_NORTH(X)",
                 0x134: "BTN_WEST(Y)", 0x136: "BTN_TL(LB)", 0x137: "BTN_TR(RB)",
                 0x13a: "BTN_SELECT", 0x13b: "BTN_START", 0x13c: "BTN_MODE",
                 0x13d: "BTN_THUMBL", 0x13e: "BTN_THUMBR"}
PAD_BUTTONS = {                  # evdev button -> one-shot action on press
    0x130: ("send", "grab"),     # A
    0x131: ("send", "release"),  # B
    0x133: ("send", "home"),     # X
    0x134: ("stop",),            # Y: stop the wheels
    0x13b: ("estop",),           # Start: E-STOP / resume (like Esc)
}
PAD_HOLD = {                     # evdev button -> action repeated while held
    0x136: ("nudge", "little_arm", +1),   # LB
    0x137: ("nudge", "little_arm", -1),   # RB
}
# D-pad: up/down = big arm, left/right = arm rotation (hat axes)
PAD_HAT_ACTIONS = {
    (PAD_ABS_HAT0X, -1): ("nudge", "rotation", +1),
    (PAD_ABS_HAT0X, +1): ("nudge", "rotation", -1),
    (PAD_ABS_HAT0Y, -1): ("nudge", "big_arm", +1),
    (PAD_ABS_HAT0Y, +1): ("nudge", "big_arm", -1),
}
# What "pad_spin" can be set to in the settings JSON if a pad's right stick is
# not the Z axis assumed below (one line in .remote_gui.json).
PAD_SPIN_CHOICES = {"ABS_Z": PAD_ABS_Z, "ABS_RZ": PAD_ABS_RZ, "ABS_RX": PAD_ABS_RX}


def _ev_ioc_read(nr, size):
    """_IOC(_IOC_READ, 'E', nr, size) - evdev's ioctl numbers."""
    return (2 << 30) | (size << 16) | (0x45 << 8) | nr


def _ev_bits(fd, ev_type, size):
    """Set of event codes of one type that the device declares."""
    buf = fcntl.ioctl(fd, _ev_ioc_read(0x20 + ev_type, size), b"\0" * size)
    return {i * 8 + j for i, b in enumerate(buf) for j in range(8) if b & (1 << j)}


class Gamepad:
    """Read one gamepad event device (/dev/input/eventN) in a background thread.

    The reader thread only touches feed()/state(); the Tk thread only reads
    snapshots and drains edge events, so no Tk call can come from the thread.
    """

    EV_FMT = "<qqHHi"                    # struct input_event (64-bit timeval)

    def __init__(self, path="auto", spin_axis=None):
        self.want = path or "auto"
        self.spin_axis = spin_axis       # optional ABS_* override for the spin axis
        self.path = None
        self.name = None
        self.note = "searching ..."
        self.layout = None               # {"lx","ly","spin","lt","rt"} axis codes
        self.events = queue.Queue()      # ("button"|"axis"|"device", code, value)
        self._axes, self._buttons, self._range = {}, {}, {}
        self._last_frac = {}
        self._buf = b""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    # ---- Tk thread -------------------------------------------------------
    def state(self):
        """(axes, buttons) snapshot with the raw evdev values."""
        with self._lock:
            return dict(self._axes), dict(self._buttons)

    def stick(self, code):
        """Axis as -1..1 around its centre, or None when there is no axis."""
        if code is None:
            return None
        with self._lock:
            value = self._axes.get(code)
            rng = self._range.get(code)
        if value is None or not rng:
            return None
        lo, hi, _ = rng
        half = max(1.0, (hi - lo) / 2.0)
        return max(-1.0, min(1.0, (value - (lo + hi) / 2.0) / half))

    def trigger(self, code):
        """Axis as 0..1 from its minimum, or None when there is no axis."""
        if code is None:
            return None
        with self._lock:
            value = self._axes.get(code)
            rng = self._range.get(code)
        if value is None or not rng:
            return None
        lo, hi, _ = rng
        return max(0.0, min(1.0, (value - lo) / max(1.0, hi - lo)))

    def close(self):
        self._stop.set()

    # ---- reader thread (and the tests) -----------------------------------
    def feed(self, ev_type, code, value):
        """Apply one evdev event (updates the state, pushes the edge event)."""
        with self._lock:
            if ev_type == PAD_EV_KEY:
                self._buttons[code] = value
            elif ev_type == PAD_EV_ABS:
                self._axes[code] = value
        if ev_type == PAD_EV_KEY:
            self.events.put(("button", code, value))
        elif ev_type == PAD_EV_ABS:
            with self._lock:
                rng = self._range.get(code)
            if rng:
                lo, hi, _ = rng
                frac = (value - lo) / max(1.0, hi - lo)
                last = self._last_frac.get(code)
                if last is None or abs(frac - last) > 0.15:
                    self._last_frac[code] = frac
                    self.events.put(("axis", code, value))

    def consume(self, data):
        """Parse raw input_event bytes (used by the reader loop and tests)."""
        buf = self._buf + data
        size = struct.calcsize(self.EV_FMT)
        while len(buf) >= size:
            chunk, buf = buf[:size], buf[size:]
            _, _, ev_type, code, value = struct.unpack(self.EV_FMT, chunk)
            self.feed(ev_type, code, value)
        self._buf = buf

    # ---- device handling -------------------------------------------------
    def _candidates(self):
        if self.want != "auto":
            return [self.want]
        return sorted(glob.glob("/dev/input/event*"),
                      key=lambda p: int(p.rsplit("event", 1)[-1] or 0))

    def _caps(self, fd):
        """{abs code: (min, max, value)} when the device looks like a gamepad."""
        try:
            keys = _ev_bits(fd, PAD_EV_KEY, 96)
            axes = _ev_bits(fd, PAD_EV_ABS, 8)
        except OSError:
            return None
        no_buttons = not (keys & set(range(0x120, 0x140)))
        no_dpad = not (keys & {0x220, 0x221, 0x222, 0x223})
        if not axes or (no_buttons and no_dpad):
            return None                  # keyboard/mouse/touchpad/... : skip
        out = {}
        for code in sorted(axes):
            try:
                info = struct.unpack(
                    "<6i", fcntl.ioctl(fd, _ev_ioc_read(0x40 + code, 24), b"\0" * 24))
            except OSError:
                continue
            out[code] = (info[1], info[2], info[0])          # min, max, value
        return out

    def _name_of(self, fd, path):
        try:
            buf = fcntl.ioctl(fd, _ev_ioc_read(0x06, 256), b"\0" * 256)
            name = buf.split(b"\0")[0].decode(errors="replace").strip()
            if name:
                return name
        except OSError:
            pass
        return os.path.basename(path)

    def _layout_for(self, ranges):
        """Which axis plays which role, from the axes the pad declares."""
        def pick(*codes):
            for c in codes:
                if c in ranges:
                    return c
            return None
        if PAD_ABS_GAS in ranges or PAD_ABS_BRAKE in ranges:
            # dedicated trigger axes (Stadia, ...): Z/RZ are the right stick
            rstr = self.spin_axis if self.spin_axis is not None else pick(PAD_ABS_Z, PAD_ABS_RZ)
            lt, rt = pick(PAD_ABS_BRAKE), pick(PAD_ABS_GAS)
        else:
            # xpad-style: Z/RZ are the triggers, RX/RY the right stick
            rstr = self.spin_axis if self.spin_axis is not None else pick(PAD_ABS_RX)
            lt, rt = pick(PAD_ABS_Z), pick(PAD_ABS_RZ)
        rspin = PAD_AXIS_PAIR.get(rstr)      # the right stick's vertical axis
        if rspin is not None and rspin not in ranges:
            rspin = None
        if lt is not None and lt in (rstr, rspin):
            lt = None                        # one axis, one job
        if rt is not None and rt in (rstr, rspin):
            rt = None
        if lt is not None and lt == rt:
            rt = None
        return {"lx": pick(PAD_ABS_X), "ly": pick(PAD_ABS_Y),
                "rstr": rstr, "rspin": rspin, "lt": lt, "rt": rt}

    def _layout_text(self):
        lay = self.layout or {}
        return ", ".join("%s=%s" % (role, PAD_AXIS_NAMES.get(code, "?%s" % code))
                         for role, code in sorted(lay.items()) if code is not None)

    def _run(self):
        while not self._stop.is_set():
            used = False
            for path in self._candidates():
                if self._stop.is_set():
                    break
                if self._read_loop(path):
                    used = True
                    break
            if not used:
                self.note = "none found (connect a USB / Bluetooth gamepad)"
                self._stop.wait(2.0)          # rescan: BT pads appear later too

    def _read_loop(self, path):
        """Read one device until it goes away. True when it was a gamepad."""
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            if e.errno not in (errno.EACCES, errno.ENODEV, errno.ENOENT):
                self.note = "cannot open %s (%s)" % (path, e.strerror or e)
            return False
        self._buf = b""
        try:
            ranges = self._caps(fd)
            if not ranges:
                return False                  # not a gamepad
            name = self._name_of(fd, path)
            with self._lock:
                self._range = ranges
                self._buttons = {}
                for code, (lo, hi, value) in ranges.items():
                    # Only trust the value the device reports at open when it is
                    # inside the axis' range; anything else would look like a
                    # fully deflected stick (some virtual/uinput pads report 0
                    # for a 1..255 axis) and could drive the cart on its own.
                    if lo <= value <= hi:
                        self._axes[code] = value
            self.layout = self._layout_for(ranges)
            self.path, self.name, self.note = path, name, name
            self.events.put(("device", name, self._layout_text()))
            seen, t0 = 0, time.time()
            while not self._stop.is_set():
                try:
                    ready, _, _ = select.select([fd], [], [], 0.5)
                except OSError:
                    break
                if ready:
                    try:
                        data = os.read(fd, 24 * 64)
                    except OSError:
                        break
                    if not data:
                        break
                    self.consume(data)
                    seen += 1
                elif seen == 0 and time.time() - t0 > 1.0:
                    break                     # opens but stays silent
                elif not os.path.exists(path):
                    break                     # unplugged while select() waited
        finally:
            os.close(fd)
        self.path = self.name = self.layout = None
        if seen == 0:
            self.note = "%s: no events (not a gamepad?)" % path
            return False
        return True


def key_name(event):
    """Key name that works both on X11 and through XWayland (Wayland session)."""
    k = (event.keysym or "").lower()
    if k in MOVE_KEYS or k in SERVO_KEYS or k in ("space", "escape", "p", "plus", "equal",
                                                  "minus", "kp_add", "kp_subtract",
                                                  "bracketleft", "bracketright"):
        return k
    ch = (event.char or "").lower()          # fallback: use the typed character
    return KEY_CHAR_ALIASES.get(ch, ch)


def parse_telemetry(line):
    """'T,...' -> dict of ints, or None if the line is not telemetry."""
    parts = line.split(",")
    if len(parts) != len(TELEMETRY_FIELDS) + 1 or parts[0] != "T":
        return None
    try:
        vals = [int(p) for p in parts[1:]]
    except ValueError:
        return None
    d = dict(zip(TELEMETRY_FIELDS, vals))
    d["on"] = [(d["onmask"] >> i) & 1 for i in range(5)]
    d["estop"] = bool(d["flags"] & 1)
    d["sonar_present"] = bool(d["flags"] & 2)
    d["tracker_ok"] = bool(d["flags"] & 4)
    d["wifi"] = bool(d["flags"] & 8)
    if d["dist"] < 0:
        d["dist"] = None
    return d


# ------------------------------------------------------------- bot link ---
class BotLink:
    """TCP server for the bot + one writer thread.  Events go to .events."""

    def __init__(self):
        self.events = queue.Queue()
        self._cmd = queue.Queue()
        self._lock = threading.Lock()
        self._conn = None
        self._srv = None
        self._stop = threading.Event()
        self._stop.set()
        self.peer = None
        self.listening = False
        self.ping_sent_at = 0.0
        threading.Thread(target=self._writer_loop, daemon=True).start()

    # -- public ------------------------------------------------------------
    @property
    def connected(self):
        with self._lock:
            return self._conn is not None

    def listen(self, port):
        self.shutdown()
        self._stop.clear()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", port))
        srv.listen(2)
        srv.settimeout(0.5)
        self._srv = srv
        self.listening = True
        threading.Thread(target=self._accept_loop, args=(srv,), daemon=True).start()

    def shutdown(self):
        self._stop.set()
        self.listening = False
        with self._lock:
            conn, self._conn = self._conn, None
        for s in (conn, self._srv):
            try:
                if s:
                    s.close()
            except Exception:
                pass
        self._srv = None
        self._drop_pending()

    def send(self, cmd):
        self._cmd.put(cmd)

    # -- internals ---------------------------------------------------------
    def _drop_pending(self):
        while True:
            try:
                self._cmd.get_nowait()
            except queue.Empty:
                return

    def _accept_loop(self, srv):
        while not self._stop.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(0.5)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self._lock:
                old, self._conn, self.peer = self._conn, conn, addr
            self._drop_pending()
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass
            self.events.put(("conn", addr))
            self._reader_loop(conn)
            with self._lock:
                if self._conn is conn:
                    self._conn = None
            self.events.put(("disc", addr))

    def _reader_loop(self, conn):
        buf = b""
        while not self._stop.is_set():
            try:
                data = conn.recv(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self.events.put(("line", line.decode(errors="replace").strip()))
            if len(buf) > 8192:
                buf = buf[-8192:]

    def _writer_loop(self):
        while True:
            try:
                cmd = self._cmd.get(timeout=0.25)
            except queue.Empty:
                cmd = None
            if cmd is None:
                # idle keep-alive: the bot drops the link after 6 s of silence
                if self.connected and time.time() - self.ping_sent_at > 1.5:
                    self._raw_send("ping")
                continue
            if not self.connected:
                self._drop_pending()
                continue
            self._raw_send(cmd)

    def _raw_send(self, cmd):
        with self._lock:
            conn = self._conn
        if conn is None:
            return False
        try:
            conn.sendall((cmd + "\n").encode())
            if cmd == "ping":
                self.ping_sent_at = time.time()
            return True
        except OSError as e:
            self.events.put(("err", "send failed (%s) - is the bot still there?" % e))
            return False


# ------------------------------------------------------------------- app ---
BAR_BG = "#e8e8e8"


class App:
    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.link = BotLink()
        self.speed = tk.IntVar(value=int(cfg.get("speed", 55)))
        self.step = tk.DoubleVar(value=float(cfg.get("step", 5)))
        self.brake_on = tk.BooleanVar(value=bool(cfg.get("brake", True)))
        self.brake_cm = tk.IntVar(value=int(cfg.get("brake_cm", 15)))
        self.port = tk.StringVar(value=str(cfg.get("port", 6000)))
        self.ssid = tk.StringVar(value=cfg.get("ssid", "SONY Xperia 1 V"))
        self.password = tk.StringVar(value=cfg.get("password", "password"))
        host = cfg.get("host")
        if upload_bot is not None and host not in upload_bot.preferred_ips():
            host = upload_bot.lan_ip()          # saved IP is stale / not a LAN address
        self.host = tk.StringVar(value=host or "127.0.0.1")
        self._dir_held = {}
        self._key_press = {}
        self._key_release = {}
        self._key_repeat = set()             # movement keys that auto-repeated
        self._pad_down = set()
        self._pad_time = {}
        self._wheel = None
        self._moving = False
        self._focused = True
        self._last_tel = 0.0
        self._last_dist = None
        self._rtt = None
        self.estop_latched = False
        self._grab, self._open = 40, -35
        self._uploading = False
        self._brake_logged = 0.0
        self._last_servo_key = {}
        self._wifi_ok = False
        self.log_keys = tk.BooleanVar(value=bool(cfg.get("log_keys", False)))
        self.pad_deadzone = float(cfg.get("pad_deadzone", PAD_DEADZONE))
        self.pad_spin = cfg.get("pad_spin", "")
        self.pad = Gamepad(str(cfg.get("pad", "auto")),
                           PAD_SPIN_CHOICES.get(self.pad_spin) if self.pad_spin else None)
        self._stick_engaged = set()          # pad axes currently past the deadzone
        self._pad_repeat = {}                # held pad action -> last run time
        self._last_drive = None              # last 'move'/'mix' text sent
        self._last_drive_at = 0.0
        self._pad_log_at = {}                # pad axis code -> last logged
        self._lan_ip = upload_bot.lan_ip() if upload_bot else "?"
        self._lan_ip_at = time.time()

        root.title("Maixduino cart remote control")
        root.minsize(880, 620)
        self._build()
        root.bind_all("<KeyPress>", self._on_key_press)
        root.bind_all("<KeyRelease>", self._on_key_release)
        root.bind_all("<F9>", self._debug_widget_under_pointer)
        root.bind("<FocusIn>", self._on_focus_in)
        root.bind("<FocusOut>", self._on_focus_out)
        root.bind("<Button-1>", self._bg_click)
        # Wayland note: Tk 8.6 has no Wayland backend, the window is an XWayland
        # client, and only the *focused* X window receives keys.  Ask for focus
        # (best effort) and tell the user when the window is not focused.
        root.lift()
        try:
            root.focus_force()
        except Exception:
            pass
        # When the window is mapped, Tk hands the keyboard to the first focusable
        # widget (a text box / button) - which would make the drive keys inactive
        # (and Space would press that button).  Take the focus back once the window
        # is really on screen.
        root.after(250, self._take_focus)
        root.after(1200, self._take_focus)
        try:
            self.link.listen(int(self.port.get()))
        except Exception as e:
            self.status.configure(text="cannot listen on port %s: %s" % (self.port.get(), e),
                                  fg="#b00020")
        self._pump()
        self._drive_tick()
        self._status_tick()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------- widgets ---
    def _labelframe(self, parent, text, **kw):
        f = ttk.Labelframe(parent, text=text, padding=6, **kw)
        return f

    def _build(self):
        root = self.root
        top = ttk.Frame(root, padding=(8, 6, 8, 0))
        top.pack(fill="x")

        self.status = tk.Label(top, text="starting ...", font=("TkDefaultFont", 11, "bold"),
                               anchor="w", fg="#333333")
        self.status.pack(fill="x")
        self.substatus = tk.Label(top, text="", anchor="w", fg="#555555")
        self.substatus.pack(fill="x")
        self.focus_label = tk.Label(top, anchor="w", fg="#8a5300",
                                    text="KEYBOARD: click this window once to take keyboard control")
        self.focus_label.pack(fill="x")

        # ---- connection / upload row ----
        conn = ttk.Frame(root, padding=(8, 4))
        conn.pack(fill="x")
        ttk.Label(conn, text="Server port").pack(side="left")
        self._field(ttk.Entry(conn, textvariable=self.port, width=6)).pack(side="left", padx=(4, 8))
        self.listen_btn = ttk.Button(conn, text="Stop server", command=self._toggle_listen)
        self.listen_btn.pack(side="left", padx=(0, 12))
        self.upload_btn = ttk.Button(conn, text="Upload & run on cart", command=self._upload_clicked)
        self.upload_btn.pack(side="left", padx=(0, 12))
        ttk.Button(conn, text="Reset cart (stop bot)", command=self._stop_bot).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(conn, text="log keys", variable=self.log_keys).pack(side="left", padx=(0, 8))
        tk.Label(conn, text="(code runs in RAM: press Upload again after a reset/power-up)",
                 fg="#666666").pack(side="left")
        if upload_bot is None:
            self.upload_btn.state(["disabled"])
            self.status.configure(text="pyserial is missing: %s" % _UPLOAD_IMPORT_ERROR)

        wifi = ttk.Frame(root, padding=(8, 0, 8, 4))
        wifi.pack(fill="x")
        ttk.Label(wifi, text="WiFi SSID").pack(side="left")
        self._field(ttk.Entry(wifi, textvariable=self.ssid, width=22)).pack(side="left", padx=(4, 8))
        ttk.Label(wifi, text="pass").pack(side="left")
        self._field(ttk.Entry(wifi, textvariable=self.password, width=14)).pack(side="left", padx=(4, 8))
        ttk.Label(wifi, text="this laptop (bot connects to)").pack(side="left")
        self._field(ttk.Entry(wifi, textvariable=self.host, width=16)).pack(side="left", padx=(4, 0))

        # ---- middle: sensors + arm ----
        mid = ttk.Frame(root, padding=(8, 4))
        mid.pack(fill="both", expand=False)

        # ultrasonic
        us = self._labelframe(mid, "Ultrasonic / 超声")
        us.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self.dist_label = tk.Label(us, text="--", font=("TkDefaultFont", 22, "bold"))
        self.dist_label.pack(anchor="w")
        self.dist_canvas = tk.Canvas(us, width=200, height=16, bg=BAR_BG, highlightthickness=0)
        self.dist_canvas.pack(fill="x", pady=4)
        self.dist_bar = self.dist_canvas.create_rectangle(0, 0, 0, 16, fill="#2e8b57", width=0)
        self.dist_note = tk.Label(us, text="", fg="#555555", anchor="w", justify="left")
        self.dist_note.pack(fill="x")
        br = ttk.Frame(us)
        br.pack(fill="x", pady=(6, 0))
        ttk.Checkbutton(br, text="auto-brake when <", variable=self.brake_on).pack(side="left")
        self._field(ttk.Entry(br, textvariable=self.brake_cm, width=4)).pack(side="left")
        ttk.Label(br, text="cm (forward only)").pack(side="left")

        # tracker
        tr = self._labelframe(mid, "Line tracker / 寻迹 (ADC)")
        tr.pack(side="left", fill="both", expand=True, padx=6)
        self.trk = []
        for ch in range(5):
            row = ttk.Frame(tr)
            row.pack(fill="x", pady=1)
            tk.Label(row, text="c%d" % ch, width=3, anchor="w").pack(side="left")
            cv = tk.Canvas(row, width=110, height=12, bg=BAR_BG, highlightthickness=0)
            cv.pack(side="left")
            item = cv.create_rectangle(0, 0, 0, 12, fill="#3a6ea5", width=0)
            val = tk.Label(row, text="----", width=5, anchor="e")
            val.pack(side="left", padx=(4, 0))
            flag = tk.Label(row, text="  ", width=3, bg="#dddddd")
            flag.pack(side="left", padx=(4, 0))
            self.trk.append((cv, item, val, flag))
        self.line_label = tk.Label(tr, text="line: --", anchor="w", fg="#333333",
                                   font=("TkDefaultFont", 10, "bold"))
        self.line_label.pack(fill="x", pady=(6, 0))
        trow = ttk.Frame(tr)
        trow.pack(fill="x", pady=(4, 0))
        ttk.Label(trow, text="on-line if below").pack(side="left")
        self.thr_var = tk.StringVar(value=str(self.cfg.get("thr", 2000)))
        thr_entry = self._field(ttk.Entry(trow, textvariable=self.thr_var, width=6))
        thr_entry.pack(side="left", padx=(4, 4))
        thr_entry.bind("<Return>", lambda ev: self._apply_thr())
        ttk.Button(trow, text="Apply", width=6, command=self._apply_thr).pack(side="left")

        # drive pad (mouse control - works even when the keyboard has no focus)
        drv = self._labelframe(mid, "Drive (mouse)")
        drv.pack(side="left", fill="y", padx=6)
        pad = ttk.Frame(drv)
        pad.pack()

        def pad_button(text, code, row, col, width=5):
            b = ttk.Button(pad, text=text, width=width)
            b.grid(row=row, column=col, padx=1, pady=1, sticky="ew")
            if code == "stop":
                b.bind("<ButtonPress-1>", lambda e: self._pad_press("stop"))
            else:
                b.bind("<ButtonPress-1>", lambda e, c=code: self._pad_press(c))
                b.bind("<ButtonRelease-1>", lambda e, c=code: self._pad_release(c))
                b.bind("<Leave>", lambda e, c=code: self._pad_release(c))
            return b

        pad_button("\u25b2 F", "f", 0, 1)
        pad_button("\u25c0 L", "l", 1, 0)
        pad_button("STOP", "stop", 1, 1)
        pad_button("\u25b6 R", "r", 1, 2)
        pad_button("\u25bc B", "b", 2, 1)
        pad_button("spin \u21ba", "tl", 0, 3)
        pad_button("spin \u21bb", "tr", 1, 3)
        tk.Label(drv, text="press and hold\nthe mouse button", fg="#666666",
                 justify="left").pack(pady=(4, 0))

        # single-wheel test buttons (check wiring / polarity)
        wheels = ttk.Frame(drv)
        wheels.pack(pady=(6, 0))
        tk.Label(wheels, text="one wheel:", fg="#666666").grid(row=0, column=0, columnspan=3,
                                                              sticky="w")
        for row, name in enumerate("ABCD", start=1):
            tk.Label(wheels, text=name, width=2).grid(row=row, column=0)
            for col, (text, up) in enumerate((("\u25c0", False), ("\u25b6", True)), start=1):
                b = ttk.Button(wheels, text=text, width=2)
                b.grid(row=row, column=col, padx=1, pady=1)
                b.bind("<ButtonPress-1>", lambda e, n=name, u=up: self._wheel_press(n, u))
                b.bind("<ButtonRelease-1>", lambda e, n=name: self._wheel_release(n))
                b.bind("<Leave>", lambda e, n=name: self._wheel_release(n))

        # arm / clamp
        arm = self._labelframe(mid, "Arm & clamp / 机械臂")
        arm.pack(side="left", fill="both", expand=True, padx=(6, 0))
        self.servo_rows = {}
        labels = {"big_arm": "big arm  IO3   r/f", "little_arm": "little arm IO10  t/g",
                  "rotation": "rotation IO11  y/h", "clamp": "clamp  IO12  c/v"}
        for name in SERVO_NAMES:
            row = ttk.Frame(arm)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=labels[name], width=22, anchor="w").pack(side="left")
            cv = tk.Canvas(row, width=90, height=12, bg=BAR_BG, highlightthickness=0)
            cv.pack(side="left")
            cv.create_line(45, 0, 45, 12, fill="#999999")
            item = cv.create_rectangle(44, 0, 46, 12, fill="#8a2be2", width=0)
            val = tk.Label(row, text="  0", width=5, anchor="e")
            val.pack(side="left", padx=(4, 0))
            ttk.Button(row, text="-", width=2,
                       command=lambda n=name: self._nudge(n, -1)).pack(side="left", padx=(4, 0))
            ttk.Button(row, text="+", width=2,
                       command=lambda n=name: self._nudge(n, +1)).pack(side="left")
            self.servo_rows[name] = (cv, item, val)
        self.clamp_label = tk.Label(arm, text="grab %d deg / open %d deg" % (self._grab, self._open),
                                    fg="#555555", anchor="w")
        self.clamp_label.pack(fill="x", pady=(6, 0))

        # ---- controls ----
        ctl = ttk.Frame(root, padding=(8, 4))
        ctl.pack(fill="x")
        ttk.Label(ctl, text="speed").pack(side="left")
        ttk.Scale(ctl, from_=0, to=100, orient="horizontal", variable=self.speed,
                  length=180, command=self._speed_changed).pack(side="left", padx=(4, 6))
        self.speed_label = tk.Label(ctl, text="55", width=3)
        self.speed_label.pack(side="left")
        ttk.Label(ctl, text="servo step").pack(side="left", padx=(12, 0))
        self._field(ttk.Spinbox(ctl, from_=1, to=45, increment=1, textvariable=self.step,
                                width=4)).pack(side="left", padx=(4, 12))
        ttk.Button(ctl, text="Home pose (P)", command=lambda: self._send("home")).pack(side="left", padx=2)
        self.resume_btn = ttk.Button(ctl, text="Resume (Esc)", command=self._resume)
        self.resume_btn.pack(side="left", padx=2)
        self.estop_btn = ttk.Button(ctl, text="E-STOP (Esc)", command=self._estop)
        self.estop_btn.pack(side="left", padx=2)

        # ---- log ----
        logf = self._labelframe(root, "Log")
        logf.pack(fill="both", expand=True, padx=8, pady=(4, 4))
        self.logbox = ScrolledText(logf, height=10, wrap="none")
        self.logbox.pack(fill="both", expand=True)
        self.logbox.configure(state="disabled")
        for tag, color in (("err", "#b00020"), ("ok", "#1b5e20"),
                           ("cmd", "#444444"), ("info", "#333333")):
            self.logbox.tag_configure(tag, foreground=color)

        help_txt = ("W/S fwd/back   A/D steer when driving / strafe when stopped   "
                    "Q/E spin   Space stop   Esc e-stop   R/F big arm   T/G little arm   "
                    "Y/H rotate   C/V grab/release   1-8 as above   P home (arm only, the "
                    "clamp keeps its grip)   +/- speed   [ ] step   |   mouse: use the "
                    "drive pad / +/- buttons (keys need the window focused, click once)"
                    "\ngamepad: L-stick drive + steer (strafe when stopped)   "
                    "R-stick sideways / spin in place   A/B clamp grab/release   X home   "
                    "Y stop   Start e-stop/resume   LB/RB little arm   D-pad big arm + "
                    "rotation   LT/RT speed   (analog, works even when the window is not focused)")
        tk.Label(root, text=help_txt, anchor="w", justify="left", fg="#333333").pack(
            fill="x", padx=8, pady=(0, 6))

    # ------------------------------------------------------------- log ----
    def log(self, text, tag="info"):
        stamp = time.strftime("%H:%M:%S")
        self.logbox.configure(state="normal")
        self.logbox.insert("end", "%s  %s\n" % (stamp, text), tag)
        if int(self.logbox.index("end-1c").split(".")[0]) > 400:
            self.logbox.delete("1.0", "50.0")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    # --------------------------------------------------------- commands ---
    def _send(self, cmd, tag="cmd"):
        self.link.send(cmd)
        self.log("-> " + cmd, tag)

    def _speed_changed(self, _=None):
        v = int(float(self.speed.get()))
        self.speed_label.configure(text=str(v))
        self.link.send("speed %d" % v)

    def _step_changed(self):
        pass

    def _apply_thr(self):
        try:
            value = int(float(self.thr_var.get()))
        except ValueError:
            self.log("threshold must be a number", "err")
            return
        self._send("thr %d" % value)

    def _nudge(self, name, direction):
        amount = direction * max(1, int(float(self.step.get())))
        self._send("nudge %s %d" % (name, amount))

    def _estop(self):
        self._send("estop", "err")

    def _resume(self):
        self._send("resume", "ok")

    def _toggle_estop(self):
        if self.estop_latched:
            self._resume()
        else:
            self._estop()

    def _toggle_listen(self):
        if self.link.listening:
            self.link.shutdown()
            self.listen_btn.configure(text="Listen")
            self.log("server stopped", "info")
        else:
            try:
                self.link.listen(int(self.port.get()))
            except Exception as e:
                self.log("cannot listen on port %s: %s" % (self.port.get(), e), "err")
                return
            self.listen_btn.configure(text="Stop server")
            self.log("listening on %s" % self._listen_text(), "ok")

    def _listen_text(self):
        return "%s:%s" % (self._lan_ip, self.port.get())

    # ------------------------------------------------------------ upload ---
    def _upload_clicked(self):
        if self._uploading or upload_bot is None:
            return
        self._uploading = True
        self.upload_btn.state(["disabled"])
        if not self.link.listening:
            self._toggle_listen()
        threading.Thread(target=self._upload_worker, daemon=True).start()

    def _upload_worker(self):
        def log(msg):
            self.link.events.put(("log", msg))
        try:
            upload_bot.upload(self.ssid.get(), self.password.get(), self.host.get(),
                              int(self.port.get()), run=True, log=log, run_timeout=18.0)
            self.link.events.put(("log", "upload finished - waiting for the bot to connect"))
        except Exception as e:
            self.link.events.put(("err", "upload failed: %r" % (e,)))
        finally:
            self.link.events.put(("upload_done", None))

    def _stop_bot(self):
        if upload_bot is None:
            return
        def work():
            try:
                upload_bot.reset_cart()
                self.link.events.put(("log", "cart reset (bot stopped, motors off)"))
            except Exception as e:
                self.link.events.put(("err", "cannot talk to the cart: %r" % (e,)))
        threading.Thread(target=work, daemon=True).start()

    # -------------------------------------------------------------- keys ---
    def _typing(self):
        w = self.root.focus_get()
        return isinstance(w, (tk.Entry, tk.Spinbox, tk.Text)) or \
            (hasattr(w, "winfo_class") and w.winfo_class() in ("TEntry", "TSpinbox", "Text"))

    def _take_focus(self):
        """Put the keyboard on the window itself, not on a text field or button."""
        try:
            self.root.focus_force()
        except Exception:
            self.root.focus_set()

    def _bg_click(self, event):
        """Clicking the window background takes the keyboard back from a text box."""
        w = event.widget
        cls = w.winfo_class() if hasattr(w, "winfo_class") else ""
        if not isinstance(w, (tk.Entry, tk.Text)) and cls not in ("TEntry", "TSpinbox"):
            self._take_focus()

    def _field(self, widget):
        """Text field: Return/Escape leave it, so driving keys keep working."""
        def done(event):
            self.root.focus_set()
            return "break"
        widget.bind("<Return>", done)
        widget.bind("<Escape>", done)
        return widget

    def _debug_widget_under_pointer(self, _event=None):
        """F9: log which widget is under the mouse (helps with XWayland scaling)."""
        x, y = self.root.winfo_pointerxy()
        w = self.root.winfo_containing(x, y)
        if w is None:
            self.log("F9 probe: pointer %d,%d -> no widget of this app" % (x, y))
        else:
            self.log("F9 probe: pointer %d,%d -> %s %r at +%d+%d size %dx%d" % (
                x, y, w.winfo_class(), str(w),
                w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height()))

    def _on_key_press(self, event):
        k = key_name(event)
        if self.log_keys.get():
            self.log("key down: keysym=%r char=%r code=%s -> %r"
                     % (event.keysym, event.char, event.keycode, k))
        if self._typing():
            return
        if k in MOVE_KEYS:
            now = time.time()
            if k in self._key_press:             # auto-repeat press (same key down)
                self._key_repeat.add(k)
            self._key_press[k] = now
            self._apply_input()                  # react now, not on the next tick
        elif k in SERVO_KEYS:
            now = time.time()
            if now - self._last_servo_key.get(k, 0) < 0.15:   # ignore key auto-repeat
                return
            self._last_servo_key[k] = now
            name, what = SERVO_KEYS[k]
            if what == "grab":
                self._send("grab")
            elif what == "release":
                self._send("release")
            else:
                self._nudge(name, what)
        elif k == "space":
            self._stop_all()
        elif k == "escape":
            self._toggle_estop()
        elif k == "p":
            self._send("home")
        elif k in ("plus", "equal", "kp_add"):
            self.speed.set(min(100, int(float(self.speed.get())) + 5))
            self._speed_changed()
        elif k in ("minus", "kp_subtract"):
            self.speed.set(max(0, int(float(self.speed.get())) - 5))
            self._speed_changed()
        elif k == "bracketleft":
            self.step.set(max(1, float(self.step.get()) - 1))
        elif k == "bracketright":
            self.step.set(min(45, float(self.step.get()) + 1))

    def _on_key_release(self, event):
        k = key_name(event)
        if k in MOVE_KEYS:
            self._key_release[k] = time.time()

    def _on_focus_in(self, _event=None):
        self._focused = True
        if self.log_keys.get():
            self.log("window got keyboard focus", "ok")

    def _on_focus_out(self, _event=None):
        """Losing the window must never leave the cart driving."""
        self._focused = False
        had = bool(self._key_press or self._key_release or self._pad_down or self._wheel)
        if self.log_keys.get():
            self.log("window lost keyboard focus%s" % (" (stopping)" if had else ""), "err")
        if had:
            self._key_press.clear()
            self._key_release.clear()
            self._key_repeat.clear()
            self._pad_down.clear()
            self._pad_time.clear()
            if self._wheel is not None:
                self.link.send("motor %s stop" % self._wheel[0])
                self._wheel = None
            self._moving = False
            self._last_drive = None
            self.link.send("stop")

    def _key_is_down(self, k, now):
        """Pressed-and-not-released, tolerant of the auto-repeat pattern.

        Both X11 and Wayland repeat a held key by sending release+press pairs, so
        a release is only trusted when no new press follows within a few
        milliseconds; a key that never auto-repeated stops after RELEASE_GAP_S.
        """
        p = self._key_press.get(k, 0.0)
        r = self._key_release.get(k, 0.0)
        if now - max(p, r) > MAX_KEY_HOLD_S:            # lost release: forget it
            return False
        if p > r:
            return True
        gap = REPEAT_GAP_S if k in self._key_repeat else RELEASE_GAP_S
        return (now - r) < gap

    def _pad_press(self, code):
        if self.log_keys.get():
            self.log("mouse pad press: %s" % code, "cmd")
        if code == "stop":
            self._stop_all()
            return
        self._pad_down.add(code)
        self._pad_time[code] = time.time()
        self._apply_input()                  # react now, not on the next tick

    def _wheel_press(self, name, up):
        """Press and hold one wheel <-> its forward/reverse direction."""
        self._wheel = (name, up, time.time())
        self.log("-> motor %s %s @ %d" % (name, "forward" if up else "reverse",
                                          int(float(self.speed.get()))))
        self._apply_input()                  # react now, not on the next tick

    def _wheel_release(self, name):
        if self._wheel and self._wheel[0] == name:
            self._wheel = None
            self._moving = False
            self._send("motor %s stop" % name)

    def _pad_release(self, code):
        self._pad_down.discard(code)
        self._pad_time.pop(code, None)

    def _stop_all(self):
        self._key_press.clear()
        self._key_release.clear()
        self._key_repeat.clear()
        self._pad_down.clear()
        self._pad_time.clear()
        self._moving = False
        self._last_drive = None
        if self._wheel is not None:
            self._send("motor %s stop" % self._wheel[0])
            self._wheel = None
        self._send("stop")

    def _move_vector(self, now):
        """(x, y, w) drive vector: held keys/buttons plus the gamepad sticks.

        x = forward, y = strafe right, w = spin right, each -100..100.

        Horizontal input (A/D, ←/→, mouse pad, left stick X) *steers* while the
        cart is driving and *strafes* when it is not - so W+A is a left curve
        rather than a diagonal, and A alone moves sideways.  The right stick
        gives sideways (X) and spin-in-place (Y) at any time; Q/E always spin.
        Several inputs add up.
        """
        x = h = y_only = w_only = 0.0
        for k in list(self._key_press):
            code = MOVE_KEYS.get(k)
            if code and self._key_is_down(k, now):
                role, sign = MOVE_ROLES[code]
                if role == "x":
                    x += sign * 100
                elif role == "h":
                    h += sign * 100
                else:
                    w_only += sign * 100
            elif code:
                del self._key_press[k]
                self._key_release.pop(k, None)
                self._key_repeat.discard(k)
        for code in list(self._pad_down):
            role, sign = MOVE_ROLES[code]
            if role == "x":
                x += sign * 100
            elif role == "h":
                h += sign * 100
            else:
                w_only += sign * 100
        lay = self.pad.layout
        if lay:
            x -= self._stick_axis(lay["ly"]) * 100          # stick up = forward
            h += self._stick_axis(lay["lx"]) * 100          # steer / strafe
            y_only += self._stick_axis(lay["rstr"]) * 100   # right stick = sideways
            w_only += self._stick_axis(lay["rspin"]) * 100  # ... and spin in place
        weight = min(1.0, abs(x) / 100.0)                   # driving? then steer
        return x, h * (1.0 - weight) + y_only, h * weight + w_only

    # -------------------------------------------------- gamepad mapping ---
    def _stick_axis(self, axis):
        """One analog axis as -1..1 with deadzone + hysteresis (0 = centred)."""
        value = self.pad.stick(axis)
        if value is None:
            return 0.0
        if axis in self._stick_engaged:
            if abs(value) < PAD_RELEASE:
                self._stick_engaged.discard(axis)
                return 0.0
            return value
        if abs(value) >= self.pad_deadzone:
            self._stick_engaged.add(axis)
            return value
        return 0.0

    def _pad_arm_tick(self, now):
        """Held pad buttons / D-pad repeat the arm nudge every PAD_REPEAT_S."""
        _, buttons = self.pad.state()
        actions = []
        for code, action in PAD_HOLD.items():
            if buttons.get(code):
                actions.append((("btn", code), action))
        for axis in (PAD_ABS_HAT0X, PAD_ABS_HAT0Y):
            value = self.pad.stick(axis)
            if value is None:
                continue
            if value <= -PAD_HAT:
                actions.append((("hat", axis, -1), PAD_HAT_ACTIONS[(axis, -1)]))
            elif value >= PAD_HAT:
                actions.append((("hat", axis, +1), PAD_HAT_ACTIONS[(axis, +1)]))
        for key, action in actions:
            if now - self._pad_repeat.get(key, 0.0) > PAD_REPEAT_S:
                self._pad_repeat[key] = now
                self._run_pad_action(action)

    def _pad_speed_trim(self, now):
        """Triggers trim the speed slider while held (LT down, RT up)."""
        lay = self.pad.layout or {}
        for key, axis, delta in (("spd-", lay.get("lt"), -5), ("spd+", lay.get("rt"), +5)):
            travel = self.pad.trigger(axis)
            if travel is not None and travel >= PAD_TRIGGER \
                    and now - self._pad_repeat.get(key, 0.0) > PAD_REPEAT_S:
                self._pad_repeat[key] = now
                self._set_speed(max(0, min(100, int(float(self.speed.get())) + delta)))

    def _run_pad_action(self, action):
        what, args = action[0], action[1:]
        if what == "nudge":
            self._nudge(args[0], args[1])
        elif what == "send":
            self._send(args[0])
        elif what == "stop":
            self._stop_all()
        elif what == "estop":
            self._toggle_estop()

    def _pad_event(self, kind, code, value):
        """One gamepad edge event, run in the Tk thread."""
        if kind == "device":
            self.log("pad: %s  [%s]" % (code, value), "ok")
            return
        if self.log_keys.get():
            if kind == "axis":
                now = time.time()                       # throttle: a moving stick
                if now - self._pad_log_at.get(code, 0.0) < 0.25:   # would flood the
                    return                                          # log and the Tk loop
                self._pad_log_at[code] = now
                name = PAD_AXIS_NAMES.get(code, "axis %d" % code)
            else:
                name = PAD_BTN_NAMES.get(code, "button %d" % code)
            self.log("pad: %s %s -> %s" % (kind, name, value))
        if kind == "button" and value:
            action = PAD_BUTTONS.get(code)
            if action:
                self._run_pad_action(action)

    def _set_speed(self, value):
        self.speed.set(value)
        self._speed_changed()

    # --------------------------------------------------------- drive tick ---
    def _apply_input(self):
        """Send the motion the current input state (keys, mouse pad, gamepad,
        wheel test) asks for.  Runs every DRIVE_TICK_MS and again right after a
        press, so the cart reacts immediately instead of waiting for the tick.
        """
        now = time.time()
        self._pad_speed_trim(now)
        self._pad_arm_tick(now)
        x, y, w = self._move_vector(now)
        if not self.link.connected or self.estop_latched:
            self._moving = False
            return
        if self._wheel is not None:
            name, up, _ = self._wheel
            self.link.send("motor %s %s %d" % (name, "f" if up else "r",
                                               int(float(self.speed.get()))))
            self._moving = True
            return
        if abs(x) > 0.5 or abs(y) > 0.5 or abs(w) > 0.5:
            speed = int(float(self.speed.get()))
            brake = (self.brake_on.get() and x > 0.5 and self._last_dist is not None
                     and self._last_dist <= int(self.brake_cm.get()))
            if brake:
                if self._moving or now - self._brake_logged > 1.0:
                    self._brake_logged = now
                    self.log("AUTO-BRAKE: obstacle at %d cm" % self._last_dist, "err")
                    self._send("stop")
                self._moving = False
            else:
                self._send_drive(x, y, w, speed)
                self._moving = True
        elif self._moving:
            self._last_drive = None              # next drive command goes out at once
            self._send("stop")
            self._moving = False

    def _send_drive(self, x, y, w, speed):
        """One drive step: 'move' for a single component (older bots understand
        it too), 'mix' when several inputs blend - steering on the fly.

        The command is only re-sent when it changes (or after KEEPALIVE_S, well
        inside the bot's 600 ms watchdog): a steady input would otherwise push
        16 identical packets a second through the slow ESP32/WiFi link.
        """
        x, y, w = int(round(x)), int(round(y)), int(round(w))
        if len([c for c in (x, y, w) if c]) == 1:
            axis = 0 if x else (1 if y else 2)      # exactly one input: plain move
            value = (x, y, w)[axis]
            code = {0: ("b", "f"), 1: ("l", "r"), 2: ("tl", "tr")}[axis][value > 0]
            spd = max(1, int(round(abs(value) * speed / 100.0)))
            cmd = "move %s %d" % (code, spd)
            label = "-> move %s @ %d" % (MOVE_LABEL[code], spd)
        else:
            cmd = "mix %d %d %d %d" % (x, y, w, speed)
            label = "-> mix fwd=%d strafe=%d spin=%d @ %d" % (x, y, w, speed)
        now = time.time()
        if cmd == self._last_drive and now - self._last_drive_at < DRIVE_KEEPALIVE_S:
            return
        if cmd != self._last_drive and not self._moving:
            self.log(label)
        self._last_drive, self._last_drive_at = cmd, now
        self.link.send(cmd)

    def _drive_tick(self):
        """Keep the input->cart path alive while a key/button is held."""
        self._apply_input()
        self.root.after(DRIVE_TICK_MS, self._drive_tick)

    # ----------------------------------------------------------- events ---
    def _pump(self):
        try:
            while True:
                kind, payload = self.link.events.get_nowait()
                if kind == "line":
                    self._handle_line(payload)
                elif kind == "conn":
                    self.log("bot connected from %s:%s" % payload, "ok")
                elif kind == "disc":
                    self.log("bot disconnected", "err")
                    self._last_tel = 0.0
                elif kind == "err":
                    self.log(payload, "err")
                elif kind == "log":
                    self.log(payload, "info")
                elif kind == "upload_done":
                    self._uploading = False
                    self.upload_btn.state(["!disabled"])
        except queue.Empty:
            pass
        try:
            while True:                          # gamepad edge events
                self._pad_event(*self.pad.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(50, self._pump)

    def _handle_line(self, line):
        if not line:
            return
        if line[0] == "T":
            d = parse_telemetry(line)
            if d:
                self._update_telemetry(d)
        elif line[0] == "P":
            if self.link.ping_sent_at:
                self._rtt = (time.time() - self.link.ping_sent_at) * 1000
        elif line[0] == "E":
            self.log("bot error: " + line[2:], "err")
        elif line[0] == "L":
            msg = line[2:]
            self.log("bot: " + msg, "info")
            if "hello" in msg:
                for part in msg.split():
                    if part.startswith("grab="):
                        self._grab = int(part[5:])
                    elif part.startswith("open="):
                        self._open = int(part[5:])
                self.clamp_label.configure(text="grab %d deg / open %d deg" % (self._grab, self._open))
        else:
            self.log("bot?: " + line, "info")

    def _update_telemetry(self, d):
        self._last_tel = time.time()
        self._last_dist = d["dist"]
        self.estop_latched = d["estop"]
        self._wifi_ok = d["wifi"]

        # ultrasonic
        if d["dist"] is not None:
            self.dist_label.configure(text="%d cm" % d["dist"], fg="#1b5e20")
            frac = max(0.0, min(200.0, d["dist"])) / 200.0
            color = "#2e8b57" if d["dist"] > 40 else ("#d9a300" if d["dist"] > 15 else "#c62828")
            self._set_bar(self.dist_canvas, self.dist_bar, frac, color)
        else:
            self.dist_label.configure(text="--", fg="#888888")
            self._set_bar(self.dist_canvas, self.dist_bar, 0.0, "#bbbbbb")
        if not d["sonar_present"]:
            self.dist_note.configure(text="no ultrasonic sensor detected")
        elif d["dist"] is None:
            self.dist_note.configure(text="no echo (nothing in range?)")
        else:
            self.dist_note.configure(text="")

        # tracker
        on_list = []
        for ch in range(5):
            val = d["c%d" % ch]
            cv, item, lbl, flag = self.trk[ch]
            if val < 0:
                lbl.configure(text="----")
                self._set_bar(cv, item, 0.0, "#dddddd")
                flag.configure(bg="#dddddd", text="  ")
                continue
            lbl.configure(text="%4d" % val)
            frac = max(0.0, min(4095.0, val)) / 4095.0
            on = d["on"][ch]
            self._set_bar(cv, item, frac, "#3a6ea5")
            if ch < 4 and on:
                on_list.append(ch)
                flag.configure(bg="#c62828", text="ON")
            else:
                flag.configure(bg="#dddddd", text="  ")
        if not d["tracker_ok"]:
            self.line_label.configure(text="line: tracker read failed", fg="#b00020")
        elif on_list:
            pos = sum(on_list) / float(len(on_list))
            if pos < 1.0:
                text = "left"
            elif pos > 2.0:
                text = "right"
            else:
                text = "center"
            self.line_label.configure(text="line: %s   (channels %s, pos %.1f/3)"
                                           % (text, ",".join(str(c) for c in on_list), pos),
                                      fg="#1b5e20")
        else:
            self.line_label.configure(text="line: none (all channels bright / off the line)", fg="#555555")

        # servos
        for name in SERVO_NAMES:
            cv, item, lbl = self.servo_rows[name]
            deg = d[name]
            lbl.configure(text="%+4d" % deg)
            frac = (max(-90, min(90, deg)) + 90) / 180.0
            self._set_bar(cv, item, frac, "#8a2be2", marker=True)

    def _set_bar(self, canvas, item, frac, color, marker=False):
        """Fill a bar (marker=False) or move a small marker (marker=True)."""
        w = canvas.winfo_width() or int(canvas["width"])
        h = canvas.winfo_height() or int(canvas["height"])
        frac = max(0.0, min(1.0, frac))
        if marker:
            x = 2 + frac * (w - 4)
            canvas.coords(item, x - 2, 0, x + 2, h)
        else:
            canvas.coords(item, 1, 0, max(1.0, 1 + frac * (w - 2)), h)
        canvas.itemconfigure(item, fill=color)

    def _status_tick(self):
        now = time.time()
        if upload_bot is not None and now - self._lan_ip_at > 10.0:
            self._lan_ip_at = now                # refresh rarely: lan_ip() spawns
            self._lan_ip = upload_bot.lan_ip()   # 'ip addr' (too slow for 4 Hz)
        if self.link.connected:
            if now - self._last_tel < STALE_TELEMETRY_S:
                state, color = "bot connected - telemetry live", "#1b5e20"
            else:
                state, color = "bot connected - no telemetry (%.1fs)" % (now - self._last_tel), "#d9a300"
        elif self.link.listening:
            state, color = "waiting for the bot ...", "#333333"
        else:
            state, color = "server stopped", "#666666"
        rtt = "  RTT %d ms" % self._rtt if self._rtt else ""
        up = ""
        if now - self._last_tel < STALE_TELEMETRY_S:
            up = "  estop LATCHED" if self.estop_latched else ""
        self.status.configure(text=state + rtt + up, fg="#b00020" if self.estop_latched else color)
        note = "listening on %s   |   bot wifi: %s   |   pad: %s" % (
            self._listen_text() if self.link.listening else "-",
            "ok" if (now - self._last_tel < STALE_TELEMETRY_S and self._wifi_ok) else "?",
            self.pad.note)
        if self._typing():
            note += "   |   KEYBOARD -> text field (click the background to drive again)"
        self.substatus.configure(text=note)
        if self._focused:
            self.focus_label.configure(text="KEYBOARD ACTIVE (click anywhere / mouse pad also works)",
                                       fg="#1b5e20")
        else:
            self.focus_label.configure(
                text="KEYBOARD NOT FOCUSED - click this window to drive with the keyboard "
                     "(Wayland/XWayland does not send keys to unfocused windows)",
                fg="#8a5300")
        self.root.after(250, self._status_tick)

    # ------------------------------------------------------------- close ---
    def _on_close(self):
        try:
            self.link._raw_send("stop")
            self.link._raw_send("estop")     # make sure the cart is left safe
        except Exception:
            pass
        cfg = {
            "port": self.port.get(), "ssid": self.ssid.get(), "password": self.password.get(),
            "host": self.host.get(),
            "speed": int(float(self.speed.get())), "step": float(self.step.get()),
            "brake": bool(self.brake_on.get()), "brake_cm": int(self.brake_cm.get()),
            "thr": self.thr_var.get(), "log_keys": bool(self.log_keys.get()),
            "pad": self.pad.want, "pad_deadzone": self.pad_deadzone,
            "pad_spin": self.pad_spin,
        }
        try:
            with open(SETTINGS, "w") as f:
                json.dump(cfg, f, indent=1)
        except Exception:
            pass
        self.pad.close()
        self.link.shutdown()
        self.root.destroy()


def main():
    cfg = {}
    if os.path.exists(SETTINGS):
        try:
            cfg = json.load(open(SETTINGS))
        except Exception:
            cfg = {}
    root = tk.Tk()
    App(root, cfg)
    root.mainloop()


if __name__ == "__main__":
    main()
