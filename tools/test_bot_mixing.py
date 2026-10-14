"""Offline test of remote_bot.py's mecanum mixing - no cart needed.

Stubs the K210 modules, loads the bot's definitions, and checks that

  * the derived MOVE_PATTERNS still match movement_reference.md exactly,
  * apply_move() keeps the old per-motor behaviour for all six movements,
  * apply_mix() produces the expected per-wheel duty differentials
    (steering while driving), including the saturation case, and
  * the 'mix' command drives, clamps its arguments, refuses while the
    e-stop is latched and feeds the watchdog.

Run:  python3 tools/test_bot_mixing.py
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def check(cond, what):
    print(("  OK   " if cond else "  FAIL ") + what)
    if not cond:
        fails.append(what)


# ------------------------------------------------------------- stubs ----
machine = types.ModuleType("machine")


class Timer:
    TIMER0, TIMER1, TIMER2 = 0, 1, 2
    CHANNEL0, CHANNEL1, CHANNEL2, CHANNEL3 = 0, 1, 2, 3
    MODE_PWM = 0

    def __init__(self, tim, ch, mode=None):
        self.tim, self.ch, self.mode = tim, ch, mode


class PWM:
    def __init__(self, t, freq=0, duty=0, pin=None, enable=True):
        self.tim, self.freq, self.duty_v, self.pin = t, freq, duty, pin

    def duty(self, v):
        self.duty_v = v


machine.Timer = Timer
machine.PWM = PWM
sys.modules["machine"] = machine

Maix = types.ModuleType("Maix")


class GPIO:
    GPIO0, GPIO1, IN, OUT, PULL_UP = 0, 1, 0, 1, 2

    def __init__(self, *a, **kw):
        pass

    def value(self, *a):
        return 1


Maix.GPIO = GPIO
sys.modules["Maix"] = Maix

fm_mod = types.ModuleType("fpioa_manager")


class _FPIOA:
    GPIO0, GPIO1 = 56, 57
    GPIOHS10, GPIOHS11, GPIOHS12 = 34, 35, 36
    SPI1_D0, SPI1_D1, SPI1_SCLK = 70, 71, 83

    def set_function(self, pin, func):
        pass


class fm:
    fpioa = _FPIOA()

    @staticmethod
    def register(pin, func, force=False):
        pass


fm_mod.fm = fm
sys.modules["fpioa_manager"] = fm_mod

net = types.ModuleType("network")


class _ESP32:
    def __init__(self, **kw):
        raise OSError("no ESP32 in the simulator")


net.ESP32_SPI = _ESP32
sys.modules["network"] = net

src = open(os.path.join(HERE, "..", "remote_bot.py")).read()
defs = src.rsplit("\ntry:", 1)[0]          # drop the auto-run tail
ns = {"__name__": "remote_bot_mix_test"}
exec(compile(defs, "remote_bot.py", "exec"), ns)
ns["setup_motors"]()
ns["setup_servos"]()


class FakeConn:
    def __init__(self):
        self.sent = []

    def send(self, data):
        self.sent.append(data.decode().strip())
        return len(data)


def duties():
    """Duty on every motor pin after letting the ramp settle."""
    ns["MOTOR_RAMP_DUTY_PER_TICK"] = 0      # snap instead of ramping
    ns["service_motors"]()
    out = {}
    for name in "ABCD":
        for role in ("rev", "fwd"):
            pwm = ns["MOTORS"][name][role]
            if pwm and pwm.duty_v:
                out["%s.%s" % (name, role)] = pwm.duty_v
    return out


# --------------------------------------------- patterns vs the reference ----
print("--- MOVE_PATTERNS still match movement_reference.md ---")
REFERENCE = {                              # F = forward pin, R = reverse pin
    "f":  {"A": "F", "B": "F", "C": "F", "D": "F"},
    "b":  {"A": "R", "B": "R", "C": "R", "D": "R"},
    "l":  {"A": "R", "B": "F", "C": "F", "D": "R"},
    "r":  {"A": "F", "B": "R", "C": "R", "D": "F"},
    "tl": {"A": "R", "B": "F", "C": "R", "D": "F"},
    "tr": {"A": "F", "B": "R", "C": "F", "D": "R"},
}
check(ns["MOVE_PATTERNS"] == REFERENCE, "derived patterns == vendor table")

print("--- apply_move() keeps the old behaviour (duty = speed on F pins) ---")
for code, pat in sorted(REFERENCE.items()):
    ns["stop_motors"]()
    ok = ns["apply_move"](code, 60)
    got = duties()
    want = {}
    for wheel, role in pat.items():
        want[("%s.%s" % (wheel, "fwd" if role == "F" else "rev")).replace("fwd", "fwd")] = 60
    want = {"%s.%s" % (w, "fwd" if r == "F" else "rev"): 60 for w, r in pat.items()}
    check(ok and got == want, "move %-2s -> %s" % (code, got))

# ------------------------------------------------------------- mixing ----
print("--- apply_mix() differentials (spd 60) ---")
CASES = [
    # (x, y, w, expected non-zero duties)  - hand-checked mecanum maths
    ((100, 0, 0),    {"A.fwd": 60, "B.fwd": 60, "C.fwd": 60, "D.fwd": 60}),
    ((0, 100, 0),    {"A.fwd": 60, "B.rev": 60, "C.rev": 60, "D.fwd": 60}),
    ((0, 0, 100),    {"A.fwd": 60, "B.rev": 60, "C.fwd": 60, "D.rev": 60}),
    # diagonals: two wheels at full duty, the other two stopped
    ((100, 100, 0),  {"A.fwd": 60, "D.fwd": 60}),
    ((100, -100, 0), {"B.fwd": 60, "C.fwd": 60}),
    # curves: half the wheels drive, cart turns while it moves
    ((100, 0, 100),  {"A.fwd": 60, "C.fwd": 60}),
    ((100, 0, -100), {"B.fwd": 60, "D.fwd": 60}),
    # steering off-centre: the differential is what steers (50/150 -> 20/60)
    ((100, -50, 0),  {"A.fwd": 20, "B.fwd": 60, "C.fwd": 60, "D.fwd": 20}),
    ((100, 50, 0),   {"A.fwd": 60, "B.fwd": 20, "C.fwd": 20, "D.fwd": 60}),
    # saturation: 300/-100/100/100 divided by the peak (300) -> 60/20/20/20
    ((100, 100, 100), {"A.fwd": 60, "B.rev": 20, "C.fwd": 20, "D.fwd": 20}),
    # stopped
    ((0, 0, 0),      {}),
]
for vec, want in CASES:
    ns["stop_motors"]()
    ns["apply_mix"](vec[0], vec[1], vec[2], 60)
    got = duties()
    check(got == want, "mix %-16s -> %s" % (str(vec), got))

# ------------------------------------------------------------- command ----
print("--- the 'mix' command ---")
conn = FakeConn()
ns["stop_motors"]()
ns["handle_command"](conn, "mix 100 -50 0 60")
got = duties()
check(got == {"A.fwd": 20, "B.fwd": 60, "C.fwd": 60, "D.fwd": 20},
      "mix command drives: %s" % got)
check(ns["move_dir"] not in (None,) and str(ns["move_dir"]).startswith("mix"),
      "watchdog sees the mix (move_dir=%r)" % (ns["move_dir"],))

ns["stop_motors"]()
conn.sent[:] = []
ns["handle_command"](conn, "mix 500 -500 0 60")           # clamped to +-100
got = duties()
check(got == {"B.fwd": 60, "C.fwd": 60},
      "arguments clamped to the forward-left diagonal: %s" % got)

ns["stop_motors"]()
conn.sent[:] = []
ns["handle_command"](conn, "mix 100 0")                   # too few arguments
check(any("mix" in s for s in conn.sent) and duties() == {},
      "bad mix reported, nothing driven (%r)" % (conn.sent,))

ns["estop_latched"] = True
conn.sent[:] = []
ns["handle_command"](conn, "mix 100 0 0 60")
check(any("estop" in s for s in conn.sent) and duties() == {},
      "mix refused while latched (%r)" % (conn.sent,))
ns["estop_latched"] = False

print()
print("FAILED: %d" % len(fails))
for f in fails:
    print("  - " + f)
sys.exit(1 if fails else 0)
