# sonar_test.py  --  超声测距测试 / HC-SR04 ultrasonic distance test (v3)
#
# Based on 程序案例/35.超声测距.txt :
#   Trig = IO31, Echo = IO30 (SDA/SCL positions, 弱5V sensor port)
#
# v3: auto-detects the wire order! At startup it probes both
#     Trig=IO31/Echo=IO30 and Trig=IO30/Echo=IO31 and uses
#     whichever one answers.
#
# Prints a series of distance measurements, then a summary.
# Put your hand / an object in front of the sensor while it runs.

from Maix import GPIO
from fpioa_manager import fm
import time

# ==================== config ====================
ORDERS = [(31, 30), (30, 31)]   # candidate (trig, echo) pin pairs to probe
SAMPLES = 30       # how many measurements to take
INTERVAL = 0.15    # seconds between measurements
TIMEOUT_US = 30000 # give up on a ping after 30 ms (echo pulse max ~25 ms)
# ================================================

try:
    _td = time.ticks_diff
except AttributeError:
    def _td(a, b):
        return a - b


def set_pin_func(pin, func):
    """Route a pin to a peripheral function (new API, old fallback)."""
    try:
        fm.fpioa.set_function(pin, func)
    except Exception:
        fm.register(pin, func)


def make_pins(trig_pin, echo_pin):
    set_pin_func(trig_pin, fm.fpioa.GPIO0)
    set_pin_func(echo_pin, fm.fpioa.GPIO1)
    trig = GPIO(GPIO.GPIO0, GPIO.OUT)
    echo = GPIO(GPIO.GPIO1, GPIO.IN)
    return trig, echo


def ping(trig, echo):
    """One HC-SR04 ping. Distance in cm, or None if no echo came back."""
    trig.value(0)
    time.sleep_us(2)
    trig.value(1)
    time.sleep_us(10)
    trig.value(0)
    t0 = time.ticks_us()
    while echo.value() == 0:                     # wait for echo rising edge
        if _td(time.ticks_us(), t0) > TIMEOUT_US:
            return None
    t1 = time.ticks_us()
    while echo.value() == 1:                     # measure echo pulse width
        if _td(time.ticks_us(), t1) > TIMEOUT_US:
            return None
    t2 = time.ticks_us()
    return _td(t2, t1) * 0.0343 / 2              # cm


# ---------------- detect the wiring ----------------
print("sonar test: probing wire order...")
chosen = None
for tp, ep in ORDERS:
    trig, echo = make_pins(tp, ep)
    time.sleep_ms(60)
    for k in range(5):
        d = ping(trig, echo)
        if d is not None and 0.5 < d < 450:
            chosen = (tp, ep, trig, echo)
            print("sensor detected: Trig=IO%d Echo=IO%d" % (tp, ep))
            break
        time.sleep_ms(60)
    if chosen:
        break

if chosen is None:
    print("summary: NO sensor found (tried IO31/IO30 and IO30/IO31)")
    print("check wiring: data wires in the SDA/SCL holes, VCC -> 5V, GND -> G")
    print("sonar test: done")
else:
    tp, ep, trig, echo = chosen
    print("taking %d samples, move something in front..." % SAMPLES)
    ok = 0
    total = 0.0
    dmin = None
    dmax = None
    for i in range(SAMPLES):
        d = ping(trig, echo)
        if d is None:
            print("%2d:  --      (no echo)" % (i + 1))
        else:
            ok += 1
            total += d
            if dmin is None or d < dmin:
                dmin = d
            if dmax is None or d > dmax:
                dmax = d
            print("%2d: %6.1f cm" % (i + 1, d))
        time.sleep(INTERVAL)
    if ok:
        print("summary: %d/%d readings ok, min %.1f / avg %.1f / max %.1f cm" % (ok, SAMPLES, dmin, total / ok, dmax))
    else:
        print("summary: sensor detected but no readings?!")
    print("sonar test: done")
