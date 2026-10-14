# Remote control over WiFi / 远程控制使用说明

Drive the cart and move the arm from this laptop, with live sensor feedback.

| File | Where it runs | What it is |
|------|---------------|------------|
| `remote_bot.py` | on the cart (K210 / MaixPy) | joins the WiFi, connects back to the laptop, drives motors + servos, streams sensor data |
| `tools/remote_gui.py` | this laptop | Tk window: TCP server, keyboard driving, sensor display, "Upload & run on cart" button |
| `tools/upload_bot.py` | this laptop | the same upload without the GUI (reset + send to RAM + start) |

The bot is a TCP **client** and the GUI is the TCP **server** (the K210's socket
cannot listen), so the cart does not need a fixed IP — only the laptop does, and
the GUI shows it.

## Quick start / 快速开始

1. Put the laptop and the cart on the **same WiFi network**, and make sure it is
   a **2.4 GHz** network (the Maixduino's ESP32 cannot see 5 GHz).
   Android hotspot: *Settings → Network & internet → Hotspot & tethering →
   Wi-Fi hotspot → "Maximize compatibility" / band 2.4 GHz*.
2. Start the GUI:  `python3 tools/remote_gui.py`
3. Click **Upload & run on cart** (the cart must be on USB; the serial port is
   `/dev/ttyUSB0`).  The cart resets, receives the program and starts it; the
   status line turns green when it is connected.
4. Drive with the keyboard.  The sensor panels update ~5 times per second.

The cart does **nothing** on power-up until you press Upload (the program lives
in RAM, see *Why RAM?* below), so press it again after every reset or power-up.

## Keyboard / 按键

| Keys | Action |
|------|--------|
| `W`/`S` or `↑`/`↓` | forward / backward |
| `A`/`D` or `←`/`→` | strafe left / right (mecanum) |
| `Q` / `E` | spin left / right |
| `Space` | stop the wheels |
| `Esc` | **E-STOP** (latched: wheels stop, servos go loose; press again to resume) |
| `R` / `F` | big arm up / down (`r`/`f`) |
| `T` / `G` | little arm up / down |
| `Y` / `H` | arm rotation left / right |
| `C` / `V` | clamp: grab / release |
| `1`…`8` | same as above: 1 big arm down, 2 up, 3 little down, 4 up, 5 rotate left, 6 rotate right, 7 release, 8 grab |
| `P` | home pose (`SERVO_HOME`) |
| `+` / `-` | drive speed |
| `[` / `]` | servo step size |

Keyboard input stops driving while you are typing in a text field (the status
line tells you); click the window background to drive again.  Movement keeps
being sent while a key is held; when you let go, the wheels stop.

**Steering while driving / 边走边转向:** held directions *combine* instead of
replacing each other, so `W`+`A` is the forward-left diagonal and `W`+`Q`
drives forward while spinning left (a curve).  The GUI sends the blend to the
bot, which gives every wheel its own duty - the mecanum "RPM differential"
(`mix` in the protocol table below).  Two opposite keys cancel out; releasing
one key falls back to the remaining one.

### How the steering mix works / 混合驱动原理

The bot describes any motion with three signed components - forward `x`,
strafe-right `y`, spin-right `w`, each −100…100 - and each wheel is the sum of
its three signed parts:

| wheel | components | straight | diagonal fwd-right | curve fwd + spin right |
|-------|-----------|----------|--------------------|------------------------|
| A | `x + y + w` | 55 | 55 | 55 |
| B | `x − y − w` | 55 | 0  | 0 |
| C | `x − y + w` | 55 | 0  | 55 |
| D | `x + y − w` | 55 | 55 | 0 |

The fastest wheel always turns at the drive speed (the rest are scaled down
proportionally), so blending never exceeds the speed you set - the *difference*
between the wheels is what curves the cart.  `move f`/`l`/`tl`/… is the same
maths with a single component; the full table and the offline test are in
`tools/test_bot_mixing.py`.

**Keyboard focus (Wayland / XWayland):** Tk 8.6 has no Wayland backend, so this
window runs through X11 compatibility (XWayland) and only receives keys while it
is the **focused** window.  The GUI shows the state on its third line:

* `KEYBOARD ACTIVE` (green) - keys drive the cart.
* `KEYBOARD NOT FOCUSED ...` (orange) - click the window once.
* `KEYBOARD -> text field` - a text box has the focus (you are typing);
  press Enter or click the window background to hand the keyboard back.

If keys seem dead, tick **log keys** next to the connection row: every key the
window receives is then written to the log.  Nothing appears = the window does
not have focus (or the compositor is not delivering keys); odd names appear =
a keymap quirk.  **F9** logs which widget is under the mouse pointer, which
helps when a scaled desktop makes coordinates confusing.

The **Drive (mouse)** pad (press and hold its buttons) does the same as the
movement keys, and the servo `-`/`+` buttons, `Home`, `Resume`, `E-STOP` work with
the mouse too - so the cart can be driven without any keyboard input.

**Auto-brake** (on by default): while driving *forward*, the GUI stops sending
motion if the ultrasonic distance is below the threshold (15 cm default).

Response time: a press is sent to the cart **immediately** (no tick wait), the
input is re-sent every 60 ms while held (the bot's watchdog needs one every
600 ms), and a release stops the wheels within ~100 ms.  The wheels themselves
ramp up/down softly (`MOTOR_RAMP_DUTY_PER_TICK` in `remote_bot.py`, default 6
duty units per 20 ms loop tick ≈ 180 ms from 0 to 55): raise it (e.g. 12) if
the cart feels sluggish to start, but watch the shared 5 V rail - the servos
and the ESP32 brown out if the motors draw current too abruptly.

## Gamepad / 手柄 (USB or Bluetooth)

A game controller connected to **this laptop** works as an input method next to
the keyboard and the mouse pad.  Pair/plug it with the desktop first (Bluetooth
settings, or just the USB cable); the GUI reads the Linux event device
(`/dev/input/event*`) on its own - no extra software, no configuration.  The
third status line shows `pad: <name>`, and the log line
`pad: <name> [lx=ABS_X, ly=ABS_Y, spin=ABS_Z, ...]` tells you which axis the
GUI picked for what (handy when a pad wires things unusually).

| Controller | Action |
|------------|--------|
| left stick | **analog drive**: forward/backward + strafe at once (push it diagonally to steer while driving) |
| right stick X | **analog spin** left / right (adds to the drive vector = curve while moving) |
| `A` / `B` | clamp grab / release |
| `X` | home pose (`SERVO_HOME`) |
| `Y` | stop the wheels (like `Space`) |
| `Start` | **E-STOP** / resume (like `Esc`) |
| `LB` / `RB` | little arm up / down (repeats while held) |
| D-pad ↑/↓ | big arm up / down (repeats while held) |
| D-pad ←/→ | arm rotation left / right (repeats while held) |
| `LT` / `RT` | drive speed − / + (repeats while held) |

The stick values are analog: half a stick sends roughly half the slider speed,
and pushing the left stick off-centre while the right stick spins gives a
smooth curve - the bot receives the three components and gives every wheel its
own duty (`mix` below).  Releasing a stick stops the wheels (same watchdog and
auto-brake as the keys).  Unlike the keyboard the controller **keeps working
while the window is not focused** - convenient when you watch the cart instead
of the screen - so only use a controller you can reach quickly, and remember
`Start` = E-STOP.

The reader uses the standardised evdev codes (`BTN_SOUTH`, `ABS_X`, `ABS_HAT0Y`,
...) plus each axis' min/max, so Xbox-style pads, DualShock/DualSense, 8BitDo
and the Google Stadia all map the same way.  Tick **log keys** to see what a pad
sends (`pad: axis ABS_Z -> ...`, `pad: button BTN_NORTH(X) -> 1`), then adjust
`PAD_BUTTONS`, `PAD_HOLD`, `PAD_HAT_ACTIONS` and the `PAD_ABS_*` roles at the
top of `tools/remote_gui.py`.  Settings in `tools/.remote_gui.json`:
`"pad": "/dev/input/event4"` pins a device, `"pad_deadzone": 0.35` changes the
stick deadzone, and `"pad_spin": "ABS_RZ"` (or `ABS_RX`) overrides which axis
the right stick uses when a pad reports it on another axis.

Notes / 注意:

* **Google Stadia**: pair with `Stadia + Y` held until the light blinks
  (Bluetooth) or just use the USB-C cable.  Over Bluetooth its right stick
  arrives on `ABS_Z`/`ABS_RZ` and the triggers on `ABS_GAS`/`ABS_BRAKE` - both
  are handled automatically (the log shows the layout it found).
* No `pad:` line at all?  Check `ls -l /dev/input/event*` (plug in first) and
  that the device is readable (`crw-rw-r-- root input`).
* The controller **cannot** be connected to the cart's ESP32: that module runs
  the WiFi + tracker-ADC firmware over an SPI link to the K210, so it cannot
  act as a Bluetooth HID host.  A controller on the *laptop* (this feature) or
  a second BT-capable microcontroller wired to the K210 would be needed; the
  laptop route is what this GUI does - it works over WiFi wherever the cart is
  in range.

The GUI's input logic can be re-checked without any hardware:
`python3 tools/test_gui_logic.py` (keys / mouse pad / focus / wheel buttons /
steering mixes) and `python3 tools/test_gui_pad.py` (latency + a virtual
Stadia-like gamepad created through `/dev/uinput`).  Both need a display and no
cart; `python3 tools/test_bot_mixing.py` checks the mecanum maths offline.

## Safety / 安全

* **Watchdog in the bot**: if no `move` command arrives for 600 ms the motors
  stop by themselves.  Closing the GUI, pulling the WiFi or killing the laptop
  app therefore always stops the cart.
* **Link timeout**: if the cart hears nothing from the GUI for 6 s it drops the
  connection, stops the motors and reconnects (the GUI pings every 1.5 s).
* **E-STOP** is latched until you resume.
* On startup the wheels stay still and the servos ease gently to `SERVO_HOME`.
* Emergency stop by hand: the **RST button** on the board, or unplug USB/power.

## Stopping / restarting the cart program

`Ctrl-C` does **not** interrupt a running MaixPy program over the serial port
(the REPL only reads input while it is waiting at a prompt), so:

* **Reset cart (stop bot)** button in the GUI, `python3 tools/upload_bot.py --stop`,
  the board's **RST** button, or a power cycle — all reset the board, which stops
  the program and leaves the motors off.
* Then press **Upload & run on cart** again to start it.

## Protocol / 协议 (if you want to extend it)

One command per line, from the GUI to the bot:

| Command | Meaning |
|---------|---------|
| `move <f\|b\|l\|r\|tl\|tr> [speed]` | drive; must be repeated at least every 600 ms (the GUI re-sends it every 60 ms while an input is held) |
| `mix <fwd> <strafe> <spin> [speed]` | steer while driving: three components −100…100 (forward, strafe right, spin right) blended into per-wheel duty differentials; same watchdog as `move` |
| `stop` | stop the wheels |
| `srv <name> <deg>` | servo to an absolute angle (`big_arm`, `little_arm`, `rotation`, `clamp`, 1…4) |
| `nudge <name> <delta>` | move a servo by a relative angle |
| `grab` / `release` | clamp to `GRAB_DEG` / `OPEN_DEG` |
| `home` | all servos to `SERVO_HOME` |
| `estop` / `resume` | latch / clear the emergency stop |
| `speed <0-100>` | default drive speed |
| `motor <A-D> <f\|r\|stop> [speed]` | drive ONE wheel only (wiring/polarity test; same watchdog as `move`) |
| `thr <n>` | tracker "on the line" threshold (ADC below n = black) |
| `tel` / `ping` | ask for telemetry / heartbeat (`P,<ms>` comes back) |

Lines from the bot:

```
L,hello ip=… fw=… sonar=(31, 30) servos={…} grab=40 open=-35   boot banner, once per connect
T,<ms>,<dist>,<c0>,<c1>,<c2>,<c3>,<c4>,<onmask>,<ba>,<la>,<rot>,<cl>,<flags>   ~5x per second
E,<text>    command/parse error          L,<text>    bot log line        P,<ms>   ping reply
```

`dist` is cm (`-1` = no echo).  `onmask` bit *i* = tracker channel *i* is on the
line.  `flags`: bit0 = estop latched, bit1 = ultrasonic sensor present,
bit2 = tracker read ok, bit3 = WiFi connected.

## Cart-side configuration / 车上配置

All in the `config` block at the top of `remote_bot.py`:

* `WIFI_SSID` / `WIFI_PASS` / `SERVER_HOST` / `SERVER_PORT` — the first four lines
  are rewritten by the uploader **and** by the GUI (from its own fields), so you
  can normally leave them alone.
* `SERVO_PINS` — `big_arm: 3, little_arm: 10, rotation: 11, clamp: 12`.
* `MOTOR_PINS` — `A: (21, 22), B: (23, 24), C: (15, 32), D: (14, 13)`
  (reverse, forward) as in `movement_reference.md`.
* `SERVO_LIMITS`, `SERVO_HOME`, `GRAB_DEG`, `OPEN_DEG`, `SERVO_STEP_DEG`, `SPEED`.
* `TRACKER_THRESH` (2000), `WATCHDOG_MS` (600), `TELEMETRY_MS` (200).

### Wheel polarity / 轮子方向 (this cart is mirrored)

On the real cart **forward and reverse are swapped** compared with
`movement_reference.md` and the vendor demos, so the program inverts every motor
(`INVERT_ALL_MOTORS = True`).  With that, `move f` really drives forward.

### IO13 (D9) is a motor pin - keep servos off it

Motor D is wired to the header pins **D8/D9 = IO14/IO13** (through DIP switches
SW1 #7/#8), and the big-arm servo's socket is **D13 = IO3** (the vendor's 4th
servo pin, header pin "13").  Earlier versions had the big-arm servo on IO13,
which shows up as wheel D trouble:

* **the big-arm 50 Hz signal goes through SW1 #8 straight into motor D's driver
  input** - the "wheel D freaks out" (jitter / crawl).  The servo keeps its last
  duty on the pin, so the wheel starts creeping after the first arm move
  (`r`/`f`, `home`, or Resume after an E-STOP) and keeps going until an E-STOP
  or a re-upload;
* the motor code then has to **drop motor D's IO13 direction**, so `motor D r`
  answers "not wired" and backward / strafe left / spin right drive only three
  wheels (the cart pulls to one side).

With `SERVO_PINS["big_arm"] = 3` and the servo plugged into D13 = IO3, both
problems disappear: all four wheels work in both directions and IO13 carries
motor PWM only while wheel D is actually commanded.  (If a servo ever *has* to
share IO13, open DIP switch SW1 #8 so the driver input is disconnected: the
servo then owns the pin cleanly, wheel D keeps only IO14 = forward, and the
`setup_motors()` startup line says which movements lose a wheel.)

Software switches that still help (servos, motors and the ESP32 share the 5 V
rail - rail sag / motor noise makes servos buzz and can brown out the WiFi):

* `MOTOR_RAMP_DUTY_PER_TICK` (default 6) - wheels ramp up/down instead of
  switching full speed, which keeps current spikes, rail sag and motor noise
  away from the servos and the ESP32.  Set 0 for instant response.
* `RELEASE_SERVOS_WHILE_DRIVING = True` - the arm goes loose whenever a wheel
  command is active (no servo noise, no rail load from the servos), but a
  grabbed object is dropped when you drive.

### Testing wheels and servos one at a time

The **Drive (mouse)** panel has "one wheel:" rows: `A`-`D` each with a
`◀`/`▶` button - press and hold one to run just that wheel (it also stops any
other movement).  Watch each wheel: with the mirrored wiring fixed in software,
`▶` should always be the direction that moves the cart forward, `◀` backward
(wheel D included - both directions are wired once the servo is on IO3).  Use
this to find a mis-wired or dead channel, and the servo `-`/`+` buttons (one
servo at a time) to check which pin moves which joint.

## Why RAM? / 为什么不写到 flash

The cart's SPIFFS flash filesystem is unreliable on this firmware: writing a
larger file corrupts it (`SPIFFS Error Code -10007`, files that are listed but
cannot be opened, `os.remove` failing).  That is why this project has always
loaded code into RAM over the raw REPL, and why the remote control does the
same.  A reset/reboot re-mounts the filesystem so `/flash/main.py` (the vendor
welcome program) keeps working, but the leftover `remote_config.py` /
`remote_bot.py` directory entries on the cart's flash are ghosts and can be
ignored.  Re-flashing the firmware with `kflash_gui` (in `教程资料/`) would
restore a clean filesystem — the bot works fine without that.

## Troubleshooting / 排查

* **"WiFi connect failed"** — 2.4 GHz band not enabled, wrong password, or the
  hotspot is out of range.  The cart prints the networks it can see on the
  serial console (visible in the GUI log).
* **GUI says "waiting for the bot"** — check the *this laptop* address in the
  GUI matches the WiFi interface (not a Tailscale/Mihomo address; the GUI picks
  the LAN one automatically), that the bot was uploaded with the same port, and
  that no firewall blocks inbound TCP 6000.
* **"no ultrasonic sensor found"** — the HC-SR04 was not answering on IO31/IO30
  or IO30/IO31 when the program started.  Check its four wires (VCC → 5V,
  GND → G, data wires in the SDA/SCL holes) **and that the cart's main power
  supply is switched on** (the USB cable alone powers the K210 and the WiFi,
  but the sensors, motors and servos need the battery/main switch).  Re-upload
  after fixing it: the pins are detected once, at program start.  The rest works
  fine without it (`dist = -1` in the telemetry).
* **Tracker channels** — idle (bright) reads ≈ 4095, over a black line ≈ 850.
  Channel `c4` is not wired on this tracker (reads 0) and is displayed anyway.
* **A servo does nothing** — wrong pin in `SERVO_PINS`.  Use `ide/servo_hold_ide.py`
  or `servo_test.py` to find which pin moves which joint, then update the map.
* **The controller does nothing** — check the third status line and
  `ls -l /dev/input/js*`.  Nothing there: the pad is not connected/pair
  finished yet, or the `joydev` module is missing.  A pad that *is* listed but
  ignored (or does the wrong thing) is a different button/axis numbering: tick
  **log keys** and remap `PAD_*` in `tools/remote_gui.py`.
* **The cart does not move but telemetry works** — `estop` is latched (red status
  line) or the speed is 0.
