# tracker_test.py  --  寻迹传感器测试 / line-tracking sensor test
#
# The tracker module (5-channel IR, "Tracking Sensor") is read through the
# ESP32's ADC channels 0-4, per 程序案例/15、adc寻迹传感器.txt :
#   ESP32 link (internal, Maixduino board):
#     cs=IO25  rst=IO8  rdy=IO9   SPI1: mosi=IO28  miso=IO26  sclk=IO27
#   ADC read: nic.adc((0,1,2,3,4))  -> 5 channel values
#
# Slide the sensor over a black line on white paper while it runs:
# channels over the line will change value.
#
# Prints one row per sample:   c0   c1   c2   c3   c4
# and a summary of each channel's min/max at the end.

import time, network
from fpioa_manager import fm

# ==================== config ====================
SAMPLES = 60        # how many readings
INTERVAL = 0.25     # seconds between readings
THRESH = 2000       # a channel reading below this counts as "on the line"
LINE_CH = (0, 1, 2, 3)   # channels actually wired on this tracker (4-channel module)
# ================================================


def set_pin_func(pin, func):
    """Route a pin to a peripheral function (new API, old fallback)."""
    try:
        fm.fpioa.set_function(pin, func)
    except Exception:
        fm.register(pin, func)


print("tracker test: connecting to the ESP32...")

set_pin_func(25, fm.fpioa.GPIOHS10)   # cs
set_pin_func(8,  fm.fpioa.GPIOHS11)   # rst
set_pin_func(9,  fm.fpioa.GPIOHS12)   # rdy
set_pin_func(28, fm.fpioa.SPI1_D0)    # mosi
set_pin_func(26, fm.fpioa.SPI1_D1)    # miso
set_pin_func(27, fm.fpioa.SPI1_SCLK)  # sclk

try:
    nic = network.ESP32_SPI(cs=fm.fpioa.GPIOHS10, rst=fm.fpioa.GPIOHS11,
                            rdy=fm.fpioa.GPIOHS12, spi=1)
    print("ESP32 SPI firmware version:", nic.version())
except Exception as e:
    print("ESP32 link FAILED: %r" % e)
    print("(the Maixduino's ESP32 module must be running - it powers up with the board)")
    raise SystemExit

print("reading 5 ADC channels, %d samples -- slide the sensor over the line..." % SAMPLES)
print("(idle reads about 4095; a channel drops to ~850 over the black line)")
cmin = [4095] * 5
cmax = [-1] * 5
ok = 0
for i in range(SAMPLES):
    try:
        adc = nic.adc((0, 1, 2, 3, 4))
    except Exception as e:
        print("%2d: read error %r" % (i + 1, e))
        continue
    ok += 1
    for c in range(5):
        if adc[c] < cmin[c]:
            cmin[c] = adc[c]
        if adc[c] > cmax[c]:
            cmax[c] = adc[c]
    on = [c for c in LINE_CH if adc[c] < THRESH]
    extra = ""
    if on:
        extra = "   <- line on " + ",".join("c%d" % c for c in on)
    print("%2d: %5d %5d %5d %5d %5d%s" % (i + 1, adc[0], adc[1], adc[2], adc[3], adc[4], extra))
    time.sleep(INTERVAL)

print("summary (%d/%d reads):" % (ok, SAMPLES))
for c in range(5):
    print("  channel %d: min %d  max %d  (change %d)" % (c, cmin[c], cmax[c], cmax[c] - cmin[c]))
print("tracker test: done")
