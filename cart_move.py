# cart_move.py  --  Maixduino (K210 / MaixPy) 麦克纳姆轮小车运动程序
#
# Demo movements: forward / backward / strafe sideways / in-place spin
# 运动: 前进 / 后退 / 左平移右平移 / 原地左转右转
#
# Based on the tutorial docs:
#   程序案例/2、麦轮车四电机控制.txt
#   程序案例/5、电机舵机同时工作.txt
#
# Motor wiring on the expansion board (2 PWM pins per motor, one per direction):
#   Motor A: IO21 / IO22
#   Motor B: IO23 / IO24
#   Motor C: IO15 / IO32
#   Motor D: IO14 / IO13
# Timer0 channels 0-3 drive motors A+B, Timer1 channels 0-3 drive motors C+D.
# On each motor the chosen pin gets the PWM duty and the other is held at 0,
# so swapping the two pins reverses that wheel.

from machine import Timer, PWM
import time

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


def car_left(a, b, c, d):                   # 左平移 (move sideways to the left)
    PWM1.duty(a)
    PWM2.duty(0)
    PWM3.duty(0)
    PWM4.duty(b)
    PWM5.duty(0)
    PWM6.duty(c)
    PWM7.duty(d)
    PWM8.duty(0)


def car_right(a, b, c, d):                  # 右平移 (move sideways to the right)
    PWM1.duty(0)
    PWM2.duty(a)
    PWM3.duty(b)
    PWM4.duty(0)
    PWM5.duty(c)
    PWM6.duty(0)
    PWM7.duty(0)
    PWM8.duty(d)


def car_turn_left(a, b, c, d):              # 原地左转 (spin in place, counter-clockwise)
    PWM1.duty(a)
    PWM2.duty(0)
    PWM3.duty(0)
    PWM4.duty(b)
    PWM5.duty(c)
    PWM6.duty(0)
    PWM7.duty(0)
    PWM8.duty(d)


def car_turn_right(a, b, c, d):             # 原地右转 (spin in place, clockwise)
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


# ---------------- demo sequence ----------------
SPEED = 60      # motor duty, 0-100
STEP = 1.5      # seconds per movement
PAUSE = 0.4     # seconds stopped between movements


def demo():
    stop()
    print("cart demo: start in 2s (speed=%d)" % SPEED)
    time.sleep(2.0)
    moves = [
        ("forward        (前进)",        car_forward),
        ("backward       (后退)",        car_backward),
        ("sideways left  (左平移)",      car_left),
        ("sideways right (右平移)",      car_right),
        ("spin left      (原地左转)",    car_turn_left),
        ("spin right     (原地右转)",    car_turn_right),
    ]
    for name, fn in moves:
        print("-> %s" % name)
        fn(SPEED, SPEED, SPEED, SPEED)
        time.sleep(STEP)
        stop()
        time.sleep(PAUSE)
    print("cart demo: done, motors stopped")


try:
    demo()
finally:
    stop()
# Tip: delete the demo() call above to import this file as a library only.
