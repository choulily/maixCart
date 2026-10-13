# Motor control reference / 电机控制对照表

Pin map (from `教程资料/程序案例/2、麦轮车四电机控制.txt`, 500 kHz PWM, duty 0–100):

> **Note:** on the actual cart the wheels turn mirrored compared with the table
> below (forward/reverse swapped), so `remote_bot.py` inverts every motor
> (`INVERT_ALL_MOTORS = True`).  All eight motor pins are in use: IO13 (= D9) is
> motor D's second direction pin, so the big-arm servo belongs on IO3 (= D13),
> not on IO13 - see `remote_control.md`.

| Motor | Reverse pin (PWM obj) | Forward pin (PWM obj) |
|-------|----------------------|-----------------------|
| A     | IO21 (PWM1)          | IO22 (PWM2)           |
| B     | IO23 (PWM3)          | IO24 (PWM4)           |
| C     | IO15 (PWM5)          | IO32 (PWM6)           |
| D     | IO14 (PWM7)          | IO13 (PWM8)           |

Per-movement motor states (`F` = forward pin = duty S, reverse pin = 0;
`R` = reverse pin = duty S, forward pin = 0; S = speed value, e.g. 60):

| Movement          | Motor A | Motor B | Motor C | Motor D |
|-------------------|---------|---------|---------|---------|
| forward   前进     | F       | F       | F       | F       |
| backward  后退     | R       | R       | R       | R       |
| left      左平移   | R       | F       | F       | R       |
| right     右平移   | F       | R       | R       | F       |
| turn_left 原地左转 | R       | F       | R       | F       |
| turn_right原地右转 | F       | R       | F       | R       |

Expanded pin-level values (S = speed):

| Movement          | PWM1(21) | PWM2(22) | PWM3(23) | PWM4(24) | PWM5(15) | PWM6(32) | PWM7(14) | PWM8(13) |
|-------------------|----------|----------|----------|----------|----------|----------|----------|----------|
| forward           | 0        | S        | 0        | S        | 0        | S        | 0        | S        |
| backward          | S        | 0        | S        | 0        | S        | 0        | S        | 0        |
| left              | S        | 0        | 0        | S        | 0        | S        | S        | 0        |
| right             | 0        | S        | S        | 0        | S        | 0        | 0        | S        |
| turn_left         | S        | 0        | 0        | S        | S        | 0        | 0        | S        |
| turn_right        | 0        | S        | S        | 0        | 0        | S        | S        | 0        |

Which movements exercise each motor in which direction:

| Motor | Forward-direction movements              | Reverse-direction movements             |
|-------|------------------------------------------|-----------------------------------------|
| A     | forward, right (strafe right), turn_right | backward, left, turn_left               |
| B     | forward, left, turn_left                 | backward, right, turn_right             |
| C     | forward, left, turn_right                | backward, right, turn_left              |
| D     | forward, right, turn_left                | backward, left, turn_right              |

Debug hints:

- A motor that never turns in any movement: check that motor's connector on the
  driver board (M_A…M_D), the motor cable, or the motor itself.
- A motor that turns in some movements but not others: one of its two direction
  pins is broken/nonexistent. Run `motor_test.py` (forward + reverse per motor)
  to check each direction channel individually.
- Use `motor_test.py` to identify the faulty motor, then compare with the
  "forward/reverse movements" table above.
