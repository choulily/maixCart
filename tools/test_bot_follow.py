"""Offline test of remote_bot.py's line following (the IR tracker controller).

Stubs the K210 modules *and* the ESP32 ADC link, then feeds synthetic tracker
rows to the follower and checks that

  * line_error() weights the channels correctly (wide line, single sensor,
    off the row, mirrored order),
  * follow_start() refuses without the line / while the e-stop is latched,
  * follow_step() strafes *towards* the line and keeps the wheels equal when
    the line is centred,
  * a lost line stops the cart after FOLLOW_LOST_MS,
  * manual commands, 'stop' and the fast brake end follow mode,
  * the 'follow' command parses and reports.

Run:  python3 tools/test_bot_follow.py
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
ns = {"__name__": "remote_bot_follow_test"}
exec(compile(defs, "remote_bot.py", "exec"), ns)
ns["setup_motors"]()
ns["setup_servos"]


class FakeNIC:
    """Stands in for the ESP32 link: adc() returns whatever row we set."""

    WHITE, LINE = 4095, 850

    def __init__(self):
        self.row = [self.WHITE] * 5
        self.calls = 0

    def adc(self, chans):
        self.calls += 1
        return tuple(self.row)

    def isconnected(self):
        return True


class FakeConn:
    def __init__(self):
        self.sent = []

    def send(self, data):
        self.sent.append(data.decode().strip())
        return len(data)


nic = FakeNIC()
ns["NIC"] = nic
ns["setup_servos"]()
ns["setup_motors"]()


def row(*dark):
    """Tracker row with the given channels over the black line."""
    r = [FakeNIC.WHITE] * 5
    for ch in dark:
        r[ch] = FakeNIC.LINE
    return r


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


def reset():
    ns["stop_motors"]()
    ns["following"] = False
    ns["FOLLOW_FLIP"] = False
    ns["follow_speed"] = ns["FOLLOW_SPEED"]
    ns["fast_brake"] = False
    ns["estop_latched"] = False
    nic.calls = 0


# ------------------------------------------------------- line_error() ----
print("--- line_error(): where is the line under the sensor row? ---")
check(ns["line_error"](row())[1] == 0, "white row -> nothing seen")
e, n = ns["line_error"](row(1, 2))
check(abs(e) < 1e-9 and n == 2, "middle channels -> centred (%+.2f, %d)" % (e, n))
e, n = ns["line_error"](row(0))
check(abs(e + 1.5) < 1e-9, "line under channel 0 -> far left (%+.2f)" % e)
e, n = ns["line_error"](row(3))
check(abs(e - 1.5) < 1e-9, "line under channel 3 -> far right (%+.2f)" % e)
e, n = ns["line_error"](row(0, 1, 2, 3))
check(abs(e) < 1e-9 and n == 4, "wide line covers the row -> centred (%+.2f)" % e)
ns["FOLLOW_FLIP"] = True
e, n = ns["line_error"](row(0))
check(abs(e - 1.5) < 1e-9, "FOLLOW_FLIP swaps the sides (%+.2f)" % e)
ns["FOLLOW_FLIP"] = False
e, n = ns["line_error"]([3000, 3000, 3000, 3000])
check(n == 0, "half-lit row (floor, no line) is not 'on the line'")

# ------------------------------------------------------ follow_start() ----
print("--- follow_start(): only starts with the line under the sensor ---")
reset()
conn = FakeConn()
nic.row = row()
ns["follow_start"](conn, None, None)
check(ns["following"] is False and any("not under the sensor" in s for s in conn.sent),
      "refused with no line (%r)" % (conn.sent,))

reset()
ns["estop_latched"] = True
conn = FakeConn()
nic.row = row(1, 2)
ns["follow_start"](conn, None, None)
check(ns["following"] is False and any("estop" in s for s in conn.sent),
      "refused while the e-stop is latched")

reset()
ns["estop_latched"] = False
conn = FakeConn()
ns["follow_start"](conn, 30, None)
check(ns["following"] is True and ns["follow_speed"] == 30
      and any("follow: on" in s for s in conn.sent),
      "started with the line visible (%r)" % (conn.sent,))

# ------------------------------------------------------- follow_step() ----
print("--- follow_step(): steer towards the line (speed 45, line slightly left) ---")


def steps(*errs, t0=None):
    """Run follow_step() at a steady 100 ms cadence with the given rows."""
    t = ns["_ticks_ms"]() if t0 is None else t0
    for r in errs:
        nic.row = r
        t += 100
        ns["follow_step"](conn, t)
    return t


reset()
conn = FakeConn()
nic.row = row(1)
ns["follow_start"](conn, 45, None)
steps(row(1), row(1))                      # hold: line under c1 (left of centre)
got = duties()
check(got.get("A.fwd", 0) and got.get("A.fwd", 0) < got.get("B.fwd", 0)
      and got.get("D.fwd", 0) < got.get("C.fwd", 0) and "A.rev" not in got,
      "line left -> left wheels slow down, no wheel reverses yet (%s)" % got)

reset()
conn = FakeConn()
nic.row = row(2)
ns["follow_start"](conn, 45, None)
steps(row(2), row(2))                      # line under c2 (right of centre)
got = duties()
check(got.get("B.fwd", 0) < got.get("A.fwd", 0) and got.get("C.fwd", 0) < got.get("D.fwd", 0),
      "line right -> right wheels slow down (%s)" % got)

reset()
conn = FakeConn()
nic.row = row(1, 2)
ns["follow_start"](conn, 45, None)
steps(row(1, 2), row(1, 2))
got = duties()
check(len(set(got.values())) == 1 and len(got) == 4,
      "line centred -> all four wheels equal (%s)" % got)

reset()
conn = FakeConn()
ns["FOLLOW_FLIP"] = True                   # the mirrored sensor order
nic.row = row(1)
ns["follow_start"](conn, 45, None)
steps(row(1), row(1))
got = duties()
check(got.get("B.fwd", 0) < got.get("A.fwd", 0),
      "mirrored sensors -> the same row corrects the other way (%s)" % got)
ns["FOLLOW_FLIP"] = False

print("--- follow_step(): watchdog is fed, and a lost line stops the cart ---")
reset()
conn = FakeConn()
nic.row = row(1, 2)
ns["follow_start"](conn, 45, None)
t = steps(row(1, 2))
check(ns["move_dir"] == "follow" and ns["last_move_ms"] == t,
      "following counts as 'moving' for the watchdog")
before = duties()
t2 = steps(row(), t0=t)                    # the line disappears ...
check(duties() == before and ns["following"] is True,
      "lost line: the last command is held for a moment (%s)" % (duties(),))
ns["follow_step"](conn, t2 + 500)          # ... and the grace time runs out
check(ns["following"] is False and "line lost" in "".join(conn.sent),
      "then it stops (%r)" % (conn.sent[-2:],))
check(all(v == 0 for v in duties().values()) or duties() == {},
      "wheels stopped (%s)" % (duties(),))

# --------------------------------------------------- commands / cancel ----
print("--- commands: follow <0|1> [speed] [flip], manual takeover, brake ---")
reset()
conn = FakeConn()
nic.row = row(1, 2)
ns["handle_command"](conn, "follow 1 35 1")
check(ns["following"] is True and ns["follow_speed"] == 35 and ns["FOLLOW_FLIP"] is True
      and any("reversed" in s for s in conn.sent),
      "follow 1 35 1 starts with the mirrored order (%r)" % (conn.sent,))
conn.sent[:] = []
ns["handle_command"](conn, "follow 1 40 0")
check(ns["following"] is True and ns["follow_speed"] == 40 and ns["FOLLOW_FLIP"] is False
      and any("still on" in s for s in conn.sent),
      "re-sending the settings keeps it running (%r)" % (conn.sent,))
conn.sent[:] = []
ns["handle_command"](conn, "follow 0")
check(ns["following"] is False and any("follow: off" in s for s in conn.sent),
      "follow 0 stops it (%r)" % (conn.sent,))

reset()
conn = FakeConn()
nic.row = row(1, 2)
ns["follow_start"](conn, 45, None)
conn.sent[:] = []
ns["handle_command"](conn, "move f 60")
check(ns["following"] is False and any("manual control" in s for s in conn.sent),
      "a manual drive command takes the wheels back (%r)" % (conn.sent,))
check(ns["move_dir"] == "f", "and the manual command drives (%s)" % ns["move_dir"])

reset()
conn = FakeConn()
nic.row = row(1, 2)
ns["follow_start"](conn, 45, None)
steps(row(1, 2))
ns["handle_command"](conn, "stop")
check(ns["following"] is False and not duties(), "'stop' ends follow + stops the wheels")

reset()
conn = FakeConn()
nic.row = row(1, 2)
ns["fast_brake"] = True
ns["follow_start"](conn, 45, None)
steps(row(1, 2))
ns["handle_command"](conn, "follow 0")
got = duties()
check(ns["following"] is False and set(got) == {"A.rev", "B.rev", "C.rev", "D.rev"},
      "with the fast brake on, stopping following brakes (%s)" % got)

reset()
conn = FakeConn()
nic.row = row()
ns["handle_command"](conn, "follow 1")
check(ns["following"] is False, "follow 1 without the line does not start")

print()
print("FAILED: %d" % len(fails))
for f in fails:
    print("  - " + f)
sys.exit(1 if fails else 0)
