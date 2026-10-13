# servo_test.py  --  舵机测试 / servo test for the Maixduino cart
#
# Tests each servo (1, 2, 3, 4) SEPARATELY with slow smooth movement
# (easing, from 程序案例/6、舵机缓速转动.txt):
#   each servo:  ease to +X deg -> hold -> ease to -X deg -> hold -> back to 0
#
# 逐个测试四个舵机：缓慢转到 +X 度 -> 停 -> 缓慢转到 -X 度 -> 停 -> 回到 0 度
#
# Servos & pins (from 程序案例/3、3、4、6 舵机教程):
#   Servo 1: pin IO12 (Timer2 CH0)
#   Servo 2: pin IO11 (Timer2 CH1)
#   Servo 3: pin IO10 (Timer2 CH2)
#   Servo 4: pin IO3  (Timer2 CH3)
# PWM 50 Hz, duty = (angle + 90) / 180 * 10 + 2.5  (angle in -90..+90)
#
# To test ONE servo only, change TEST_SERVOS below, e.g.:  TEST_SERVOS = "2"

from machine import Timer, PWM
import time

# ---------------- config ----------------
TEST_SERVOS = "1234"    # which servos to test: "1", "34", "1234"...
SWEEP = {1: 45, 2: 35, 3: 35, 4: 45}   # half sweep per servo (ranges used by the vendor demos)
EASE_DELAY = 0.04       # seconds per degree while easing (smaller = faster)
HOLD = 0.8              # seconds to hold at each extreme
GAP = 1.5               # seconds between servos
# ----------------------------------------

# ---------------- PWM / servo setup ----------------
Servo1_tim = Timer(Timer.TIMER2, Timer.CHANNEL0, mode=Timer.MODE_PWM)
S1 = PWM(Servo1_tim, freq=50, duty=0, pin=12)
Servo2_tim = Timer(Timer.TIMER2, Timer.CHANNEL1, mode=Timer.MODE_PWM)
S2 = PWM(Servo2_tim, freq=50, duty=0, pin=11)
Servo3_tim = Timer(Timer.TIMER2, Timer.CHANNEL2, mode=Timer.MODE_PWM)
S3 = PWM(Servo3_tim, freq=50, duty=0, pin=10)
Servo4_tim = Timer(Timer.TIMER2, Timer.CHANNEL3, mode=Timer.MODE_PWM)
S4 = PWM(Servo4_tim, freq=50, duty=0, pin=3)

SERVOS = {
    "1": ("Servo 1 (IO12)", S1),
    "2": ("Servo 2 (IO11)", S2),
    "3": ("Servo 3 (IO10)", S3),
    "4": ("Servo 4 (IO3)",  S4),
}

_last_angle = {"1": 0, "2": 0, "3": 0, "4": 0}


def move(servo, angle):
    """Slowly ease this servo to `angle` (degrees)."""
    label, pwm = SERVOS[servo]
    last = _last_angle[servo]
    if angle >= last:
        step = 1
    else:
        step = -1
    for a in range(last, angle, step):
        pwm.duty((a + 90) / 180 * 10 + 2.5)
        time.sleep(EASE_DELAY)
    pwm.duty((angle + 90) / 180 * 10 + 2.5)
    _last_angle[servo] = angle


def test_servo(servo):
    label, pwm = SERVOS[servo]
    x = SWEEP[int(servo)]
    print("-- %s --" % label)
    print("   +%d deg" % x)
    move(servo, x)
    time.sleep(HOLD)
    print("   -%d deg" % x)
    move(servo, -x)
    time.sleep(HOLD)
    print("   back to 0 deg")
    move(servo, 0)
    time.sleep(HOLD)


def run_tests():
    servos = TEST_SERVOS
    print("servo test: servos=%s, start in 2s (will hold each position briefly)" % servos)
    # gently place all servos at 0 first
    for s in "1234":
        move(s, 0)
    time.sleep(1.0)
    for i, s in enumerate(servos):
        if s not in SERVOS:
            print("?? unknown servo %r, skipping" % s)
            continue
        test_servo(s)
        if i < len(servos) - 1:
            print("   (next servo in %ss)" % GAP)
            time.sleep(GAP)
    print("servo test: done, all servos back at 0")


try:
    run_tests()
finally:
    for s in "1234":
        try:
            move(s, 0)
        except Exception:
            pass
