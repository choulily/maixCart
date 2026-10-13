# servo_hold_ide.py  --  hold a servo at a chosen position (for mechanical adjustments)
# 舵机保持脚本：把某个舵机固定在指定角度，方便机械调整。
#
# Paste this whole file into MaixPy IDE (open -> connect -> paste -> Run).
# The selected servo stays energized at CENTER while the program runs.
#
# Release / 释放：
#   - Stop the program in the IDE (or Ctrl-C): servos go loose (no holding force)
#   - Power off or press the board's RST button
#   - Or type in the IDE console:  S3.duty(0)
#
# Servo pins (程序案例/3、4、6 舵机教程):
#   Servo 1: IO12 | Servo 2: IO11 | Servo 3: IO10 | Servo 4: IO3   (Timer2, 50 Hz)

from machine import Timer, PWM
import time

# ==================== config ====================
SERVO = "3"          # which servo to hold: "1".."4"
CENTER = 0           # angle in degrees (-85..85); 0 = middle position
HOLD_OTHERS = True   # True: also hold the other 3 servos at 0 deg (arm stays posed)
                     # False: leave the others loose
EASE_DELAY = 0.04    # seconds per degree while easing to CENTER
# ================================================

Servo1_tim = Timer(Timer.TIMER2, Timer.CHANNEL0, mode=Timer.MODE_PWM)
S1 = PWM(Servo1_tim, freq=50, duty=0, pin=12)
Servo2_tim = Timer(Timer.TIMER2, Timer.CHANNEL1, mode=Timer.MODE_PWM)
S2 = PWM(Servo2_tim, freq=50, duty=0, pin=11)
Servo3_tim = Timer(Timer.TIMER2, Timer.CHANNEL2, mode=Timer.MODE_PWM)
S3 = PWM(Servo3_tim, freq=50, duty=0, pin=10)
Servo4_tim = Timer(Timer.TIMER2, Timer.CHANNEL3, mode=Timer.MODE_PWM)
S4 = PWM(Servo4_tim, freq=50, duty=0, pin=3)

SERVOS = {"1": S1, "2": S2, "3": S3, "4": S4}


def duty_for(angle):
    return (angle + 90) / 180 * 10 + 2.5


def ease(pwm, frm, to):
    step = 1 if to >= frm else -1
    for a in range(frm, to, step):
        pwm.duty(duty_for(a))
        time.sleep(EASE_DELAY)
    pwm.duty(duty_for(to))


try:
    if HOLD_OTHERS:
        for s in "1234":
            if s != SERVO:
                SERVOS[s].duty(duty_for(0))
    angle = max(-85, min(85, CENTER))
    print("holding Servo %s at %+d deg ..." % (SERVO, angle))
    ease(SERVOS[SERVO], 0, angle)
    print("adjust now. Stop the program (or Ctrl-C) to release.")
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass
finally:
    if HOLD_OTHERS:
        for s in "1234":
            SERVOS[s].duty(0)
    else:
        SERVOS[SERVO].duty(0)
    print("released (servos loose)")
