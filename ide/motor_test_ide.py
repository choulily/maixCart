# motor_test_ide.py  --  单电机测试 / single-motor test (MaixPy IDE version)
# Paste this whole file into MaixPy IDE (open -> connect -> paste -> Run).
# 把整个文件粘贴到 MaixPy IDE 中，连接后直接运行即可。
#
# Tests each motor (A, B, C, D) SEPARATELY:
#   for each motor:  forward for STEP seconds -> stop -> reverse for STEP seconds -> stop
# 逐个单独测试四个电机：正转 STEP 秒 -> 停 -> 反转 STEP 秒 -> 停
#
# Watch the 4 wheels while it runs: note which motor label has NO movement
# (and whether it's dead in both directions or only one).
#
# To test ONE motor only, change TEST_MOTORS below, e.g.:  TEST_MOTORS = "B"
# 只测一个电机时，把 TEST_MOTORS 改成 "B" 即可。

from machine import Timer, PWM
import time

# ---------------- config ----------------
TEST_MOTORS = "ABCD"    # which motors to test, any order/subset: "A", "BD", "ABCD"
SPEED = 60             # motor duty, 0-100
STEP = 1.0              # seconds per direction
PAUSE = 0.1             # seconds quiet between the two directions
GAP = 0.2               # seconds quiet between motors
# ----------------------------------------

# ---------------- PWM / motor setup ----------------
# Motor pins from 程序案例/2、麦轮车四电机控制.txt:
#   Motor A: forward=IO22 (PWM2), reverse=IO21 (PWM1)
#   Motor B: forward=IO24 (PWM4), reverse=IO23 (PWM3)
#   Motor C: forward=IO32 (PWM6), reverse=IO15 (PWM5)
#   Motor D: forward=IO13 (PWM8), reverse=IO14 (PWM7)
tim1 = Timer(Timer.TIMER0, Timer.CHANNEL0, mode=Timer.MODE_PWM)
PWM1 = PWM(tim1, freq=500000, duty=0, pin=21)
tim2 = Timer(Timer.TIMER0, Timer.CHANNEL1, mode=Timer.MODE_PWM)
PWM2 = PWM(tim2, freq=500000, duty=0, pin=22)
tim3 = Timer(Timer.TIMER0, Timer.CHANNEL2, mode=Timer.MODE_PWM)
PWM3 = PWM(tim3, freq=500000, duty=0, pin=23)
tim4 = Timer(Timer.TIMER0, Timer.CHANNEL3, mode=Timer.MODE_PWM)
PWM4 = PWM(tim4, freq=500000, duty=0, pin=24)
tim5 = Timer(Timer.TIMER1, Timer.CHANNEL0, mode=Timer.MODE_PWM)
PWM5 = PWM(tim5, freq=500000, duty=0, pin=15)
tim6 = Timer(Timer.TIMER1, Timer.CHANNEL1, mode=Timer.MODE_PWM)
PWM6 = PWM(tim6, freq=500000, duty=0, pin=32)
tim7 = Timer(Timer.TIMER1, Timer.CHANNEL2, mode=Timer.MODE_PWM)
PWM7 = PWM(tim7, freq=500000, duty=0, pin=14)
tim8 = Timer(Timer.TIMER1, Timer.CHANNEL3, mode=Timer.MODE_PWM)
PWM8 = PWM(tim8, freq=500000, duty=0, pin=13)

ALL_PWM = (PWM1, PWM2, PWM3, PWM4, PWM5, PWM6, PWM7, PWM8)

# label, forward pwm, reverse pwm
MOTOR_PINS = {
    "A": ("Motor A  (forward=IO22 / reverse=IO21)", PWM2, PWM1),
    "B": ("Motor B  (forward=IO24 / reverse=IO23)", PWM4, PWM3),
    "C": ("Motor C  (forward=IO32 / reverse=IO15)", PWM6, PWM5),
    "D": ("Motor D  (forward=IO13 / reverse=IO14)", PWM8, PWM7),
}


def all_stop():
    for p in ALL_PWM:
        p.duty(0)


def test_motor(name, speed=SPEED, step=STEP):
    label, fwd, rev = MOTOR_PINS[name]
    print("-- %s --" % label)
    print("   forward %ss" % step)
    fwd.duty(speed)
    time.sleep(step)
    fwd.duty(0)
    time.sleep(PAUSE)
    print("   reverse %ss" % step)
    rev.duty(speed)
    time.sleep(step)
    rev.duty(0)
    time.sleep(PAUSE)


def run_tests():
    motors = TEST_MOTORS.upper()
    all_stop()
    print("motor test: motors=%s speed=%d, start in 2s" % (motors, SPEED))
    time.sleep(2.0)
    for i, name in enumerate(motors):
        if name not in MOTOR_PINS:
            print("?? unknown motor %r, skipping" % name)
            continue
        test_motor(name)
        if i < len(motors) - 1:
            print("   (next motor in %ss)" % GAP)
            time.sleep(GAP)
    print("motor test: done, all motors stopped")


try:
    run_tests()
finally:
    all_stop()
