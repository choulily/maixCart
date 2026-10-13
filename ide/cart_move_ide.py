# cart_move_ide.py  --  运动测试 / movement test
# Paste this whole file into MaixPy IDE (open -> connect -> paste -> Run).
# 把整个文件粘贴到 MaixPy IDE 中，连接后直接运行即可。
#
# Modes (set in the config block below):
#   TEST = "all"        : full demo, one movement after another
#                         forward -> backward -> sideways left -> sideways right
#                         -> spin left -> spin right  (前进/后退/左平移/右平移/原地左转/原地右转)
#   TEST = "forward" / "backward" / "left" / "right" / "turn_left" / "turn_right"
#                         : dedicated test of ONE movement, repeated REPEAT times
#
# Motor pins (from 程序案例/2、麦轮车四电机控制.txt):
#   Motor A: forward=IO22, reverse=IO21    Motor B: forward=IO24, reverse=IO23
#   Motor C: forward=IO32, reverse=IO15    Motor D: forward=IO13, reverse=IO14

from machine import Timer, PWM
import time

# ==================== config ====================
TEST   = "all"      # "all" or one of: forward / backward / left / right / turn_left / turn_right
SPEED  = 60         # motor duty 0-100
STEP   = 1.5        # seconds per movement (in "all" mode)
REPEAT = 3          # repeats when testing a single movement
PAUSE  = 0.5        # seconds stopped between moves
# ================================================

# ---------------- PWM / motor setup ----------------
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


# ---------------- movement functions (duty 0-100 = speed) ----------------

def car_forward(a, b, c, d):                # 前进
    PWM1.duty(0)
    PWM2.duty(a)
    PWM3.duty(0)
    PWM4.duty(b)
    PWM5.duty(0)
    PWM6.duty(c)
    PWM7.duty(0)
    PWM8.duty(d)


def car_backward(a, b, c, d):               # 后退
    PWM1.duty(a)
    PWM2.duty(0)
    PWM3.duty(b)
    PWM4.duty(0)
    PWM5.duty(c)
    PWM6.duty(0)
    PWM7.duty(d)
    PWM8.duty(0)


def car_left(a, b, c, d):                   # 左平移 (move sideways left)
    PWM1.duty(a)
    PWM2.duty(0)
    PWM3.duty(0)
    PWM4.duty(b)
    PWM5.duty(0)
    PWM6.duty(c)
    PWM7.duty(d)
    PWM8.duty(0)


def car_right(a, b, c, d):                  # 右平移 (move sideways right)
    PWM1.duty(0)
    PWM2.duty(a)
    PWM3.duty(b)
    PWM4.duty(0)
    PWM5.duty(c)
    PWM6.duty(0)
    PWM7.duty(0)
    PWM8.duty(d)


def car_turn_left(a, b, c, d):              # 原地左转 (spin counter-clockwise)
    PWM1.duty(a)
    PWM2.duty(0)
    PWM3.duty(0)
    PWM4.duty(b)
    PWM5.duty(c)
    PWM6.duty(0)
    PWM7.duty(0)
    PWM8.duty(d)


def car_turn_right(a, b, c, d):             # 原地右转 (spin clockwise)
    PWM1.duty(0)
    PWM2.duty(a)
    PWM3.duty(b)
    PWM4.duty(0)
    PWM5.duty(0)
    PWM6.duty(c)
    PWM7.duty(d)
    PWM8.duty(0)


def stop():
    car_forward(0, 0, 0, 0)


MOVES = {
    "forward":    ("forward        (前进)",       car_forward),
    "backward":   ("backward       (后退)",       car_backward),
    "left":       ("sideways left  (左平移)",     car_left),
    "right":      ("sideways right (右平移)",     car_right),
    "turn_left":  ("spin left      (原地左转)",   car_turn_left),
    "turn_right": ("spin right     (原地右转)",   car_turn_right),
}


def run_single(name):
    label, fn = MOVES[name]
    print("-- dedicated test: %s  x%d --" % (label, REPEAT))
    for i in range(REPEAT):
        print("   run %d/%d" % (i + 1, REPEAT))
        fn(SPEED, SPEED, SPEED, SPEED)
        time.sleep(STEP)
        stop()
        time.sleep(PAUSE)


def run_all():
    print("movement test: full sequence")
    for name in ("forward", "backward", "left", "right", "turn_left", "turn_right"):
        label, fn = MOVES[name]
        print("-> %s" % label)
        fn(SPEED, SPEED, SPEED, SPEED)
        time.sleep(STEP)
        stop()
        time.sleep(PAUSE)


try:
    stop()
    print("movement test: start in 2s (speed=%d)" % SPEED)
    time.sleep(2.0)
    if TEST == "all":
        run_all()
    elif TEST in MOVES:
        run_single(TEST)
    else:
        print("?? unknown TEST %r (use 'all' or one of %s)" % (TEST, ", ".join(MOVES)))
    print("movement test: done, motors stopped")
finally:
    stop()
