# sonar_test_swapped.py  --  same test, but Trig=IO30 / Echo=IO31
# (for the case where the sensor's two data wires sit in the opposite holes)
from Maix import GPIO
from fpioa_manager import fm
import time

TRIG_PIN = 30
ECHO_PIN = 31

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


set_pin_func(TRIG_PIN, fm.fpioa.GPIO0)
set_pin_func(ECHO_PIN, fm.fpioa.GPIO1)

trig = GPIO(GPIO.GPIO0, GPIO.OUT)
echo = GPIO(GPIO.GPIO1, GPIO.IN)


def get_distance():
    trig.value(0)
    time.sleep_us(2)
    trig.value(1)
    time.sleep_us(10)
    trig.value(0)
    t0 = time.ticks_us()
    while echo.value() == 0:
        if _td(time.ticks_us(), t0) > 30000:
            return None
    t1 = time.ticks_us()
    while echo.value() == 1:
        if _td(time.ticks_us(), t1) > 30000:
            return None
    t2 = time.ticks_us()
    return _td(t2, t1) * 0.0343 / 2


print("sonar test (swapped): Trig=IO%d Echo=IO%d, 20 samples..." % (TRIG_PIN, ECHO_PIN))
ok = 0
for i in range(20):
    d = get_distance()
    if d is None:
        print("%2d:  --      (no echo)" % (i + 1))
    else:
        ok += 1
        print("%2d: %6.1f cm" % (i + 1, d))
    time.sleep(0.2)
print("summary: %d/20 readings ok" % ok)
print("sonar test: done")
