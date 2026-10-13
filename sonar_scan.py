# sonar_scan.py  --  find which pins the HC-SR04 is wired to
#
# Tries every ordered trig/echo combination among the free header pins:
#   IO30, IO31 (SDA/SCL area - the tutorial pins)
#   IO33, 34, 35, 36, 39 (A0..A5 analog-header area, "suitable for sensors")
# (motor pins 21-24/15/32/14/13, servo pins 12/11/10/3 and the UART pins
#  are excluded - they are already in use.)
#
# Prints an idle diagnostic per pin, then any working trig/echo pair.

from Maix import GPIO
from fpioa_manager import fm
import time

CANDIDATES = [30, 31, 33, 34, 35, 36, 39]
TIMEOUT_US = 25000     # one-way wait limit for an echo edge

try:
    _td = time.ticks_diff
except AttributeError:
    def _td(a, b):
        return a - b


def set_pin_func(pin, func):
    try:
        fm.fpioa.set_function(pin, func)
    except Exception:
        fm.register(pin, func)


# ---------------- idle diagnostics ----------------
print("idle diagnostics (input with internal pull-up / pull-down):")
for p in CANDIDATES:
    set_pin_func(p, fm.fpioa.GPIO0)
    g1 = GPIO(GPIO.GPIO0, GPIO.IN, GPIO.PULL_UP)
    time.sleep_ms(5)
    v1 = g1.value()
    g2 = GPIO(GPIO.GPIO0, GPIO.IN, GPIO.PULL_DOWN)
    time.sleep_ms(5)
    v2 = g2.value()
    print("  IO%-2d: pullup-read=%d  pulldown-read=%d" % (p, v1, v2))


def ping(trig, echo):
    trig.value(0)
    time.sleep_us(2)
    trig.value(1)
    time.sleep_us(10)
    trig.value(0)
    t0 = time.ticks_us()
    while echo.value() == 0:
        if _td(time.ticks_us(), t0) > TIMEOUT_US:
            return None
    t1 = time.ticks_us()
    while echo.value() == 1:
        if _td(time.ticks_us(), t1) > TIMEOUT_US:
            return None
    t2 = time.ticks_us()
    return _td(t2, t1) * 0.0343 / 2


# ---------------- pair scan ----------------
print("scanning trig/echo pairs...")
hits = 0
for tp in CANDIDATES:
    for ep in CANDIDATES:
        if tp == ep:
            continue
        try:
            set_pin_func(tp, fm.fpioa.GPIO0)
            set_pin_func(ep, fm.fpioa.GPIO1)
            trig = GPIO(GPIO.GPIO0, GPIO.OUT)
            echo = GPIO(GPIO.GPIO1, GPIO.IN)
            time.sleep_ms(10)
            for k in range(3):
                d = ping(trig, echo)
                if d is not None and 1.0 < d < 450.0:
                    hits += 1
                    print("HIT: trig=IO%d echo=IO%d -> %.1f cm" % (tp, ep, d))
                    break
                time.sleep_ms(30)
        except Exception as ex:
            print("  pair IO%d / IO%d error: %r" % (tp, ep, ex))

if hits:
    print("scan done: sensor found (see HIT lines above)")
else:
    print("scan done: NO sensor response on any candidate pin pair")
