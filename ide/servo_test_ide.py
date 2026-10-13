# servo_test_ide.py  --  舵机专用测试 / dedicated servo test
# Paste this whole file into MaixPy IDE (open -> connect -> paste -> Run).
# 把整个文件粘贴到 MaixPy IDE 中，连接后直接运行即可。
#
# Modes (set in the config block below):
#   TEST_MODE = "single" : dedicated test of ONE servo.
#                          swings: center -> +amp -> -amp -> ... -> center, N cycles.
#   TEST_MODE = "all"    : quick check of each servo: +sweep -> -sweep -> back to 0.
#
# Servo pins (from 程序案例/3、4、6 舵机教程):
#   Servo 1: IO12  (Timer2 CH0)     Servo 2: IO11  (Timer2 CH1)
#   Servo 3: IO10  (Timer2 CH2)     Servo 4: IO3   (Timer2 CH3)
# PWM 50 Hz, duty = (angle + 90) / 180 * 10 + 2.5   (angle in -90..+90)

from machine import Timer, PWM
import time

# ==================== config ====================
TEST_MODE = "single"    # "single" or "all"

# --- used when TEST_MODE = "single" ---
SINGLE_SERVO  = "1"     # which servo: "1".."4"
SINGLE_CENTER = 0       # center angle in degrees
SINGLE_AMP    = 45      # swing amplitude around center
SINGLE_CYCLES = 3       # number of full swings

# --- used when TEST_MODE = "all" ---
TEST_SERVOS = "1234"    # which servos to test, in order
SWEEP = {"1": 45, "2": 35, "3": 35, "4": 45}   # per-servo half sweep

# --- common ---
EASE_DELAY = 0.04       # seconds per degree while moving (bigger = slower)
HOLD       = 0.8        # seconds to hold at each position
# ================================================
#
# Dedicated test examples / 专用测试示例:
#   find the usable range of servo 2:   SINGLE_SERVO="2"; SINGLE_CENTER=0;  SINGLE_AMP=55; SINGLE_CYCLES=2
#   small wiggle to check neutral:      SINGLE_AMP=10;  SINGLE_CYCLES=3
#   test one specific position:         SINGLE_CENTER=40; SINGLE_AMP=5
#   slow careful check:                 EASE_DELAY=0.08
# -------------------------------------------------

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
    """Slowly ease this servo to `angle` (degrees, clamped to -85..85)."""
    if angle > 85:
        angle = 85
    elif angle < -85:
        angle = -85
    pwm = SERVOS[servo][1]
    last = _last_angle[servo]
    step = 1 if angle >= last else -1
    for a in range(last, angle, step):
        pwm.duty((a + 90) / 180 * 10 + 2.5)
        time.sleep(EASE_DELAY)
    pwm.duty((angle + 90) / 180 * 10 + 2.5)
    _last_angle[servo] = angle


def run_single(servo, center, amp, cycles):
    print("-- dedicated test: %s --" % SERVOS[servo][0])
    print("   center=%d amp=%d cycles=%d" % (center, amp, cycles))
    move(servo, center)
    time.sleep(HOLD)
    for i in range(cycles):
        hi = center + amp
        lo = center - amp
        print("   -> %+d deg" % hi)
        move(servo, hi)
        time.sleep(HOLD)
        print("   -> %+d deg" % lo)
        move(servo, lo)
        time.sleep(HOLD)
    print("   -> %+d deg (center)" % center)
    move(servo, center)
    time.sleep(HOLD)


def run_all(servos_str):
    for i, s in enumerate(servos_str):
        if s not in SERVOS:
            print("?? unknown servo %r, skipping" % s)
            continue
        x = SWEEP[s]
        print("-- %s --" % SERVOS[s][0])
        print("   +%d deg" % x)
        move(s, x)
        time.sleep(HOLD)
        print("   -%d deg" % x)
        move(s, -x)
        time.sleep(HOLD)
        print("   back to 0 deg")
        move(s, 0)
        time.sleep(HOLD)
        if i < len(servos_str) - 1:
            print("   (next servo in 1.5s)")
            time.sleep(1.5)


try:
    # gently place all servos at 0 first / 先让所有舵机回到 0 度
    for s in "1234":
        move(s, 0)
    time.sleep(0.5)

    if TEST_MODE == "all":
        print("servo test: all servos = %s" % TEST_SERVOS)
        run_all(TEST_SERVOS)
    else:
        run_single(SINGLE_SERVO, SINGLE_CENTER, SINGLE_AMP, SINGLE_CYCLES)

    print("servo test: done")
finally:
    for s in "1234":
        try:
            move(s, 0)
        except Exception:
            pass
