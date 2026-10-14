# remote_bot.py  --  远程控制程序 / WiFi remote-control program (runs on the Maixduino / K210)
#
# What it does / 功能:
#   * joins the WiFi network using the Maixduino's ESP32.  The ESP32 does two jobs
#     at the same time: WiFi radio + the tracker sensor's ADC bridge.
#   * connects as a TCP *client* to this laptop, where the GUI
#     (tools/remote_gui.py) is running - the K210's socket cannot listen, so the
#     GUI must be the server.  That also means the cart needs no fixed IP.
#   * receives one-line commands: drive the 4 mecanum wheels, move the 4 arm
#     servos, emergency stop, ...
#   * answers with one telemetry line ~5x per second:
#     ultrasonic distance + the 5 tracker ADC channels + servo angles
#
# Servos / 舵机 (Timer2, 50 Hz):
#   big arm    -> IO3       little arm -> IO10
#   rotation   -> IO11      clamp      -> IO12  (grab / release)
#   (change SERVO_PINS below if your arm is wired differently)
#
# Motors / 电机 (Timer0 + Timer1, 500 kHz, duty = speed 0-100):
#   A: IO21/IO22   B: IO23/IO24   C: IO15/IO32   D: IO14/IO13
#   * 'move <code>' drives one of the six movements; 'mix <fwd> <strafe> <spin>'
#     blends them (each -100..100) so the cart can be steered while it drives:
#     the wheels get per-wheel duty differentials, see WHEEL_MIX below.
#   * INVERT_ALL_MOTORS = True: this cart's wheels turn mirrored compared with the
#     vendor docs, so the forward/reverse roles are swapped here ("move f" really
#     goes forward on this cart).
#   * duty is ramped (MOTOR_RAMP_DUTY_PER_TICK) so starting/stopping does not
#     spike the 5 V rail - servos and the ESP32 share it with the motors.
#
# IO13 (= Arduino header pin D9) is one of motor D's two driver inputs on the
# expansion board (motor D = D8/D9 = IO14/IO13, DIP switch SW1 #8), so it must
# stay a motor pin.  The big-arm servo belongs on the vendor's pin IO3 (= header
# pin 13 / D13, the 4th servo socket) - that is what SERVO_PINS says.  Do NOT
# put a servo back on IO13: its 50 Hz signal would reach motor D's driver
# input through the switch (wheel D crawls/twitches) and that motor direction
# would have to be dropped from the drive patterns:
#   * with SERVO_PINS["big_arm"] = 3, wheel D drives via IO14 = forward and
#     IO13 = reverse (after the mirrored INVERT_ALL_MOTORS) -> all six
#     movements use all four wheels.
#   * if a servo ever has to share IO13, open DIP switch SW1 #8 (it disconnects
#     D9 from motor D's driver); wheel D then keeps only IO14 (forward).
#   * RELEASE_SERVOS_WHILE_DRIVING = True makes the arm go limp whenever the
#     wheels run - it stops servo noise / rail load, at the price of dropping a
#     grabbed object while driving.
#
# Safety / 安全:
#   * watchdog: if no 'move' command arrives within WATCHDOG_MS the motors stop by
#     themselves (dead WiFi / closed GUI can never leave the cart driving)
#   * if nothing is received from the GUI for LINK_TIMEOUT_MS the bot reconnects
#     (the GUI sends 'ping' every ~1.5 s, and telemetry keeps flowing the other way)
#   * 'estop' latches: motors stop, servos go loose, motion commands are ignored
#     until 'resume'
#   * startup: wheels stay still, servos ease to SERVO_HOME
#
# Stopping the bot / 停止程序:
#   Ctrl-C does NOT interrupt a running MaixPy program over the serial port (the
#   REPL only reads input while it is waiting for a prompt), so the way to stop
#   this program is to RESET the board: the GUI's "Reset cart" button, the RST
#   button on the board, or a power cycle.  tools/upload_bot.py does a reset
#   automatically before it sends the program.
#
# How to start / 启动方式 (details in remote_control.md):
#   python3 tools/upload_bot.py                    # reset + send + run (GUI-less)
#   the GUI's "Upload & run on cart" button        # same thing, one click
#   (code goes straight into RAM - this cart's flash filesystem is unreliable,
#    and the program also runs fine pasted into the MaixPy IDE)

from machine import Timer, PWM
from Maix import GPIO
from fpioa_manager import fm
import time
import socket
import network
import gc

# ==================== config / 配置 ====================
# ==== the uploader (tools/upload_bot.py / the GUI's Upload button) rewrites the
# ====   four lines below with the values you enter; everything is sent to the
# ====   cart's RAM, nothing is written to the cart's flash ====
WIFI_SSID = "SONY Xperia 1 V"
WIFI_PASS = "password"
SERVER_HOST = "10.68.159.144"   # laptop running the GUI (shown in the GUI window)
SERVER_PORT = 6000
# ==== end of cart-side config ====

SPEED = 55              # default drive speed, motor duty 0-100
WATCHDOG_MS = 600       # motors stop if no 'move' command for this long
LINK_TIMEOUT_MS = 6000  # reconnect if nothing arrives from the GUI for this long
LOOP_TICK_MS = 20       # main loop period (~50 Hz)
TELEMETRY_MS = 200      # how often a telemetry line is sent (~5 Hz)
MOTOR_RAMP_DUTY_PER_TICK = 6   # soft start/stop: duty units added per loop tick
                               # (0 = switch instantly; bigger = quicker; a ramp
                               #  keeps the current spikes / rail sag / motor noise
                               #  away from the servos and the ESP32)
RELEASE_SERVOS_WHILE_DRIVING = False   # True: arm goes limp while the wheels run
                               # (helps if the servos buzz or brown out while
                               #  driving - but a grabbed object would be dropped)
SERVO_STEP_DEG = 2.5    # servo easing speed, degrees per loop tick (~125 deg/s)
TRACKER_THRESH = 2000   # tracker ADC reading below this = "on the line"
SONAR_TIMEOUT_US = 20000  # give up on one ping after 20 ms (~3.4 m max)

# angle limits per servo (deg) - keep the arm out of the chassis / camera
SERVO_LIMITS = {
    "big_arm":    (-85, 85),
    "little_arm": (-85, 85),
    "rotation":   (-85, 85),
    "clamp":      (-85, 85),
}
# pose taken at startup and with the 'home' command
SERVO_HOME = {"big_arm": 0, "little_arm": 0, "rotation": 0, "clamp": -35}
GRAB_DEG = 40           # clamp: fingers closed around an object (vendor demo used ~35)
OPEN_DEG = -35          # clamp: fully open

# pins: motor = (reverse pin, forward pin); servo = signal pin (see header)
#   big arm = IO3 (header pin 13 / D13, the vendor's 4th servo socket);
#   IO13 is motor D's second driver input (header pin 9 / D9) - never a servo.
MOTOR_PINS = {"A": (21, 22), "B": (23, 24), "C": (15, 32), "D": (14, 13)}
SERVO_PINS = {"big_arm": 3, "little_arm": 10, "rotation": 11, "clamp": 12}

# On this cart every wheel turns the opposite way compared with the vendor docs
# (forward and reverse are mirrored), so the two pins of each motor are swapped
# here: 'move f' now really goes forward.  Set to False if the motors are ever
# re-wired the other way round.
INVERT_ALL_MOTORS = True

# ==================== small helpers ====================
try:
    _ticks_ms = time.ticks_ms
    _ticks_diff = time.ticks_diff
except AttributeError:                          # very old firmware
    _ticks_ms = lambda: int(time.time() * 1000)
    _ticks_diff = lambda a, b: a - b

ESP32_FW = "?"


def set_pin_func(pin, func):
    """Route a pin to a peripheral function (new API, old fallback)."""
    try:
        fm.fpioa.set_function(pin, func)
    except Exception:
        fm.register(pin, func)


def duty_for(angle_x10):
    """Servo duty for an angle in tenths of a degree (50 Hz: 0.5 ms .. 2.5 ms)."""
    return (angle_x10 / 10.0 + 90) / 180 * 10 + 2.5


# ==================== motors ====================
# which timer channel drives which motor pin (matches cart_move.py / the tutorial)
MOTOR_PWM_CH = {
    21: (Timer.TIMER0, Timer.CHANNEL0), 22: (Timer.TIMER0, Timer.CHANNEL1),
    23: (Timer.TIMER0, Timer.CHANNEL2), 24: (Timer.TIMER0, Timer.CHANNEL3),
    15: (Timer.TIMER1, Timer.CHANNEL0), 32: (Timer.TIMER1, Timer.CHANNEL1),
    14: (Timer.TIMER1, Timer.CHANNEL2), 13: (Timer.TIMER1, Timer.CHANNEL3),
}

# Mecanum mixing / 麦克纳姆轮混合驱动 (movement_reference.md)
#   Every movement - and any blend of them while driving - is a combination of
#   three signed axes:
#       x = forward (+) / backward (-)
#       y = strafe right (+) / strafe left (-)
#       w = spin right (+) / spin left (-)
#   Each wheel sees the sum of its three components (WHEEL_MIX below), which is
#   the classic mecanum "RPM differential": e.g. x=+100, w=+100 gives A/C the
#   full duty and B/D zero, and the cart turns right while it drives forward.
WHEEL_MIX = {                 # wheel -> (x sign, y sign, w sign)
    "A": (+1, +1, +1),
    "B": (+1, -1, -1),
    "C": (+1, -1, +1),
    "D": (+1, +1, -1),
}
MOVE_VECTORS = {              # movement code -> (x, y, w) at full deflection
    "f":  (100, 0, 0), "b": (-100, 0, 0),
    "l":  (0, -100, 0), "r": (0, 100, 0),
    "tl": (0, 0, -100), "tr": (0, 0, 100),
}
MOVE_NAMES = {"f": "forward", "b": "backward", "l": "strafe left", "r": "strafe right",
              "tl": "spin left", "tr": "spin right"}


def mix_pattern(x, y, w):
    """Per-motor pin roles ("F"/"R") of one (x, y, w) direction blend."""
    out = {}
    for name in "ABCD":
        sx, sy, sw = WHEEL_MIX[name]
        out[name] = "F" if (x * sx + y * sy + w * sw) > 0 else "R"
    return out


# movement -> per-motor direction, derived from the vectors above so the two
# can never drift apart (F = forward pin gets the duty, R = reverse pin)
MOVE_PATTERNS = {code: mix_pattern(*vec) for code, vec in MOVE_VECTORS.items()}

MOTOR_ROLES = ("rev", "fwd")
MOTORS = {}                  # "A" -> {"rev": pwm or None, "fwd": pwm or None}
MOTOR_SLOT = {}              # ("A", "rev") -> index into DUTY_NOW / DUTY_TARGET, or None
DUTY_NOW = [0] * 8           # duty currently written to each available pin
DUTY_TARGET = [0] * 8        # duty the ramp is heading for
SLOT_PWM = [None] * 8        # slot -> PWM object

SERVO = {}                   # name -> {"pwm":..., "angle10": int, "target10": int}
SERVO_ORDER = ("big_arm", "little_arm", "rotation", "clamp")

move_dir = None      # movement code currently driving, None = stopped
last_move_ms = 0     # when the last 'move' command arrived
estop_latched = False
servos_released = False
speed = SPEED
tracker_thresh = TRACKER_THRESH


def setup_motors():
    """Create the motor PWM objects; pins taken by a servo are skipped."""
    global DUTY_NOW, DUTY_TARGET, SLOT_PWM
    DUTY_NOW = [0] * 8
    DUTY_TARGET = [0] * 8
    SLOT_PWM = [None] * 8
    servo_pins = set(SERVO_PINS.values())
    dropped = []
    slot = 0
    for name in "ABCD":
        rev_pin, fwd_pin = MOTOR_PINS[name]
        if INVERT_ALL_MOTORS:                  # this cart is wired mirrored
            rev_pin, fwd_pin = fwd_pin, rev_pin
        MOTORS[name] = {}
        for role, pin in (("rev", rev_pin), ("fwd", fwd_pin)):
            if pin in servo_pins:
                owner = [s for s, p in SERVO_PINS.items() if p == pin][0]
                dropped.append("motor %s %s pin IO%d is used by servo '%s'" % (
                    name, role, pin, owner))
                MOTORS[name][role] = None
                MOTOR_SLOT[(name, role)] = None
                continue
            tim, ch = MOTOR_PWM_CH[pin]
            t = Timer(tim, ch, mode=Timer.MODE_PWM)
            MOTORS[name][role] = PWM(t, freq=500000, duty=0, pin=pin)
            MOTOR_SLOT[(name, role)] = slot
            SLOT_PWM[slot] = MOTORS[name][role]
            slot += 1
    if dropped:
        print("remote_bot: WARNING - pin conflict, dropping motor channels:")
        for d in dropped:
            print("             " + d)
    movements_ok = []
    movements_weak = []
    for code in ("f", "b", "l", "r", "tl", "tr"):
        missing = [n for n in "ABCD"
                   if MOTOR_SLOT[(n, "fwd" if MOVE_PATTERNS[code][n] == "F" else "rev")] is None]
        if missing:
            movements_weak.append("%s (no %s)" % (MOVE_NAMES[code], ",".join(missing)))
        else:
            movements_ok.append(MOVE_NAMES[code])
    print("remote_bot: movements with all 4 wheels: %s" % (", ".join(movements_ok) or "none"))
    if movements_weak:
        print("remote_bot: movements missing a wheel: %s" % (", ".join(movements_weak)))


def set_duty(motor, up, duty):
    """Aim one motor pin at a duty (the ramp moves it there). None = not wired."""
    index = MOTOR_SLOT.get((motor, "fwd" if up else "rev"))
    if index is not None:
        DUTY_TARGET[index] = duty


def apply_mix(x, y, w, spd):
    """Aim the wheels at a direction blend (ramped by service_motors).

    x = forward/backward, y = strafe right/left, w = spin right/left, each in
    -100..100.  Every wheel gets the signed sum of the three components, scaled
    so the fastest wheel turns at `spd` duty: the per-wheel differential steers
    the cart while it drives.
    """
    comp = {}
    for name in "ABCD":
        sx, sy, sw = WHEEL_MIX[name]
        comp[name] = x * sx + y * sy + w * sw
    peak = max([100] + [abs(v) for v in comp.values()])
    for name in "ABCD":
        duty = abs(comp[name]) * spd / peak
        up = comp[name] > 0
        set_duty(name, up, int(duty + 0.5))
        set_duty(name, not up, 0)
    return True


def apply_move(code, spd):
    """Aim the wheels at one of the six movements (ramped by service_motors)."""
    vec = MOVE_VECTORS.get(code)
    if not vec:
        return False
    apply_mix(vec[0], vec[1], vec[2], spd)
    return True


def drive_one_motor(name, up, spd):
    """Aim a single wheel (used by the wheel test commands)."""
    if name not in MOTORS:
        return False
    for other in "ABCD":                       # everything else stops
        set_duty(other, True, 0)
        set_duty(other, False, 0)
    set_duty(name, up, spd)
    return True


def service_motors():
    """Soft-start/stop: move the real duty towards the target a step at a time."""
    step = MOTOR_RAMP_DUTY_PER_TICK
    for i in range(8):
        now, target = DUTY_NOW[i], DUTY_TARGET[i]
        if now == target:
            continue
        if step <= 0:                          # ramping disabled
            now = target
        elif now < target:
            now = min(target, now + step)
        else:
            now = max(target, now - step)
        DUTY_NOW[i] = now
        pwm = SLOT_PWM[i]
        if pwm:
            pwm.duty(now)


def stop_motors():
    """Stop now (no ramp) - this is the safety path."""
    global move_dir
    move_dir = None
    for i in range(8):
        DUTY_TARGET[i] = 0
        if DUTY_NOW[i]:
            DUTY_NOW[i] = 0
    for name in "ABCD":
        for role in MOTOR_ROLES:
            pwm = MOTORS[name][role]
            if pwm:
                pwm.duty(0)


# ==================== servos ====================
def setup_servos():
    """Create the servo PWM objects (Timer2 channels, in SERVO_ORDER)."""
    for i, name in enumerate(SERVO_ORDER):
        t = Timer(Timer.TIMER2, i, mode=Timer.MODE_PWM)
        SERVO[name] = {"pwm": PWM(t, freq=50, duty=0, pin=SERVO_PINS[name]),
                       "angle10": 0, "target10": 0}


def servo_target(name, deg):
    """Set a servo target angle in degrees. Returns True if the servo exists."""
    if name not in SERVO:
        return False
    lo, hi = SERVO_LIMITS.get(name, (-85, 85))
    t = int(deg * 10)
    if t < lo * 10:
        t = lo * 10
    elif t > hi * 10:
        t = hi * 10
    SERVO[name]["target10"] = t
    return True


def servo_nudge(name, delta_deg):
    if name not in SERVO:
        return False
    return servo_target(name, (SERVO[name]["target10"] + int(delta_deg * 10)) / 10.0)


def service_servos():
    """Ease every servo one step towards its target (called from the main loop)."""
    if servos_released:
        return
    step = int(SERVO_STEP_DEG * 10)
    if step < 1:
        step = 1
    for name in SERVO_ORDER:
        s = SERVO[name]
        cur, tgt = s["angle10"], s["target10"]
        if cur == tgt:
            continue
        nxt = cur + step if tgt > cur else cur - step
        if (tgt > cur and nxt > tgt) or (tgt < cur and nxt < tgt):
            nxt = tgt
        s["angle10"] = nxt
        s["pwm"].duty(duty_for(nxt))


def release_servos():
    """Cut the servo signals - the arm goes loose (no holding force)."""
    global servos_released
    servos_released = True
    for name in SERVO_ORDER:
        SERVO[name]["pwm"].duty(0)


def hold_servos():
    """Re-apply the signal at the current position (undo release_servos)."""
    global servos_released
    servos_released = False
    for name in SERVO_ORDER:
        s = SERVO[name]
        s["pwm"].duty(duty_for(s["angle10"]))


# ==================== sensors ====================
class Sonar:
    """HC-SR04; probes both wire orders at startup (like sonar_test.py)."""

    def __init__(self):
        self.trig = None
        self.echo = None
        self.pins = None

    def _make(self, trig_pin, echo_pin):
        set_pin_func(trig_pin, fm.fpioa.GPIO0)
        set_pin_func(echo_pin, fm.fpioa.GPIO1)
        self.trig = GPIO(GPIO.GPIO0, GPIO.OUT)
        self.echo = GPIO(GPIO.GPIO1, GPIO.IN)

    def ping(self):
        """Distance in cm, or None when no echo came back."""
        if self.trig is None:
            return None
        trig, echo = self.trig, self.echo
        trig.value(0)
        time.sleep_us(2)
        trig.value(1)
        time.sleep_us(10)
        trig.value(0)
        t0 = time.ticks_us()
        while echo.value() == 0:
            if _ticks_diff(time.ticks_us(), t0) > SONAR_TIMEOUT_US:
                return None
        t1 = time.ticks_us()
        while echo.value() == 1:
            if _ticks_diff(time.ticks_us(), t1) > SONAR_TIMEOUT_US:
                return None
        return _ticks_diff(time.ticks_us(), t1) * 0.0343 / 2

    def detect(self):
        """Find the trig/echo order that answers. Returns True when found."""
        for trig_pin, echo_pin in ((31, 30), (30, 31)):
            try:
                self._make(trig_pin, echo_pin)
            except Exception as e:
                print("remote_bot: sonar pins IO%d/IO%d failed: %r" % (trig_pin, echo_pin, e))
                continue
            time.sleep_ms(60)
            for _ in range(5):
                d = self.ping()
                if d is not None and 0.5 < d < 450:
                    self.pins = (trig_pin, echo_pin)
                    print("remote_bot: sonar ok: Trig=IO%d Echo=IO%d" % self.pins)
                    return True
                time.sleep_ms(60)
        self.trig = self.echo = None
        print("remote_bot: no ultrasonic sensor found (tried IO31/IO30 and IO30/IO31)")
        return False


# ==================== ESP32 link (WiFi + tracker ADC) ====================
NIC = None                   # the ESP32_SPI object (WiFi + tracker ADC share it)
_last_esp_reset_ms = 0


def esp32_pins():
    set_pin_func(25, fm.fpioa.GPIOHS10)    # cs
    set_pin_func(8,  fm.fpioa.GPIOHS11)    # rst
    set_pin_func(9,  fm.fpioa.GPIOHS12)    # rdy
    set_pin_func(28, fm.fpioa.SPI1_D0)     # mosi
    set_pin_func(26, fm.fpioa.SPI1_D1)     # miso
    set_pin_func(27, fm.fpioa.SPI1_SCLK)   # sclk


def esp32_reset_hw():
    """Pulse the ESP32 module's reset line (IO8) - it can get stuck in the
    middle of an AT transaction if the K210 is reset while the link is busy."""
    global _last_esp_reset_ms
    print("remote_bot: resetting the ESP32 module (IO8 low 300 ms)")
    try:
        set_pin_func(8, fm.fpioa.GPIO0)
        g = GPIO(GPIO.GPIO0, GPIO.OUT)
        g.value(0)
        time.sleep_ms(300)
        g.value(1)
        _last_esp_reset_ms = _ticks_ms()
        time.sleep_ms(3000)                # let the ESP32's WiFi stack boot
    except Exception as e:
        print("remote_bot: ESP32 reset failed: %r" % (e,))


def esp32_open(allow_reset=True):
    """Create (or re-create) the ESP32 link. Returns True when it answers."""
    global NIC, ESP32_FW
    for attempt in (1, 2, 3):
        try:
            esp32_pins()
            NIC = network.ESP32_SPI(cs=fm.fpioa.GPIOHS10, rst=fm.fpioa.GPIOHS11,
                                    rdy=fm.fpioa.GPIOHS12, spi=1)
            ESP32_FW = str(NIC.version())
            print("remote_bot: ESP32 firmware %s" % ESP32_FW)
            return True
        except Exception as e:
            print("remote_bot: ESP32 did not answer (attempt %d: %r)" % (attempt, e))
            if not allow_reset or attempt >= 3:
                break
            if _ticks_diff(_ticks_ms(), _last_esp_reset_ms) < 30000:
                print("remote_bot: (already reset the ESP32 recently, waiting 2 s)")
                time.sleep_ms(2000)
                continue
            esp32_reset_hw()
    print("remote_bot: ESP32 link is not working - check the board / power")
    return False


def wifi_connect():
    """Block until the WiFi link is up (keeps trying, recovers the ESP32)."""
    global NIC
    attempts = 0
    while True:
        try:
            if NIC.isconnected():
                break
            print('remote_bot: joining WiFi "%s" ...' % WIFI_SSID)
            NIC.connect(WIFI_SSID, WIFI_PASS)
        except Exception as e:
            print("remote_bot: WiFi connect failed: %r" % (e,))
        for i in range(40):                    # wait up to 20 s for the association
            try:
                if NIC.isconnected():
                    break
            except Exception:
                break
            if i % 10 == 9:
                print("remote_bot: ... still joining WiFi (%d s)" % ((i + 1) // 2))
            time.sleep_ms(500)
        try:
            if NIC.isconnected():
                ip, mask, gw = NIC.ifconfig()
                print("remote_bot: WiFi up: ip=%s gw=%s" % (ip, gw))
                return True
        except Exception:
            pass
        attempts += 1
        try:
            seen = [s[0] for s in NIC.scan()]
            print("remote_bot: not connected yet, visible networks: %s" % (seen,))
            print("remote_bot: (check the SSID/password and that the 2.4 GHz band is on)")
        except Exception as e:
            print("remote_bot: WiFi scan failed (%r) - ESP32 link looks broken" % (e,))
            if attempts % 2 == 0:
                esp32_open()                   # re-create the link (+ reset if needed)
                attempts = 0


def tcp_connect():
    s = socket.socket()
    try:
        s.settimeout(5)
    except Exception:
        pass
    s.connect((SERVER_HOST, SERVER_PORT))
    try:
        s.settimeout(0.02)      # short recv timeout: keep polling/telemetry flowing
    except Exception:
        pass
    return s


def send_line(conn, line):
    if not line.endswith("\n"):
        line += "\n"
    data = line.encode()
    sent = 0
    while sent < len(data):
        n = conn.send(data[sent:])
        if not n:
            raise OSError("send returned 0")
        sent += n
    return True


# ==================== command handling ====================
SERVO_ALIASES = {
    "big_arm": "big_arm", "big": "big_arm", "arm": "big_arm", "ba": "big_arm", "1": "big_arm",
    "little_arm": "little_arm", "little": "little_arm", "la": "little_arm", "2": "little_arm",
    "rotation": "rotation", "rot": "rotation", "3": "rotation",
    "clamp": "clamp", "grip": "clamp", "4": "clamp",
}

# recv() error texts that just mean "nothing to read right now"
IDLE_ERRORS = ("timeout", "timed out", "again", "would block", "no data", "empty")


def is_idle_error(err):
    text = str(err).lower()
    for word in IDLE_ERRORS:
        if word in text:
            return True
    return False


def handle_command(conn, line):
    """Run one command line."""
    global move_dir, last_move_ms, estop_latched, speed, tracker_thresh
    parts = line.split()
    if not parts:
        return
    cmd = parts[0]
    args = parts[1:]

    if cmd == "move" and args:
        if estop_latched:
            send_line(conn, "E,estop is latched - send 'resume' first")
            return
        code = args[0]
        try:
            spd = int(float(args[1])) if len(args) > 1 else speed
        except Exception:
            spd = speed
        if spd < 0:
            spd = 0
        elif spd > 100:
            spd = 100
        if not apply_move(code, spd):
            send_line(conn, "E,unknown movement '%s'" % code)
            return
        move_dir = code
        last_move_ms = _ticks_ms()

    elif cmd == "mix" and len(args) >= 3:
        # blend while driving: mix <fwd> <strafe-right> <spin-right> [speed]
        if estop_latched:
            send_line(conn, "E,estop is latched - send 'resume' first")
            return
        try:
            x, y, w = [int(float(v)) for v in args[:3]]
            spd = int(float(args[3])) if len(args) > 3 else speed
        except Exception:
            send_line(conn, "E,mix needs numbers: mix <fwd> <strafe> <spin> [speed]")
            return
        x = max(-100, min(100, x))
        y = max(-100, min(100, y))
        w = max(-100, min(100, w))
        spd = max(0, min(100, spd))
        apply_mix(x, y, w, spd)
        move_dir = "mix %d %d %d" % (x, y, w)
        last_move_ms = _ticks_ms()

    elif cmd == "stop":
        stop_motors()

    elif cmd in ("motor", "wheel") and len(args) >= 2:
        name = args[0].upper()
        what = args[1].lower()
        if estop_latched:
            send_line(conn, "E,estop is latched - send 'resume' first")
        elif name not in MOTORS:
            send_line(conn, "E,unknown motor '%s' (use A, B, C or D)" % args[0])
        elif what not in ("f", "r", "stop", "s"):
            send_line(conn, "E,motor: direction must be f, r or stop")
        else:
            try:
                spd = int(float(args[2])) if len(args) > 2 else speed
            except Exception:
                spd = speed
            spd = max(0, min(100, spd))
            if what in ("stop", "s"):
                stop_motors()
                send_line(conn, "L,motor %s stopped" % name)
            else:
                up = (what == "f")
                if MOTOR_SLOT.get((name, "fwd" if up else "rev")) is None:
                    send_line(conn, "E,motor %s %s direction is not wired on this cart"
                              % (name, "forward" if up else "reverse"))
                else:
                    drive_one_motor(name, up, spd)
                    move_dir = "motor %s %s" % (name, what)
                    last_move_ms = _ticks_ms()

    elif cmd == "srv" and len(args) >= 2:
        name = SERVO_ALIASES.get(args[0])
        if estop_latched:
            send_line(conn, "E,estop is latched - send 'resume' first")
        elif name is None:
            send_line(conn, "E,unknown servo '%s'" % args[0])
        else:
            try:
                servo_target(name, float(args[1]))
            except Exception:
                send_line(conn, "E,bad angle '%s'" % args[1])

    elif cmd == "nudge" and len(args) >= 2:
        name = SERVO_ALIASES.get(args[0])
        if estop_latched:
            send_line(conn, "E,estop is latched - send 'resume' first")
        elif name is None:
            send_line(conn, "E,unknown servo '%s'" % args[0])
        else:
            try:
                servo_nudge(name, float(args[1]))
            except Exception:
                send_line(conn, "E,bad delta '%s'" % args[1])

    elif cmd == "grab" or cmd == "release":
        if estop_latched:
            send_line(conn, "E,estop is latched - send 'resume' first")
        else:
            servo_target("clamp", GRAB_DEG if cmd == "grab" else OPEN_DEG)

    elif cmd == "home":
        if estop_latched:
            send_line(conn, "E,estop is latched - send 'resume' first")
        else:
            for name in SERVO_ORDER:
                servo_target(name, SERVO_HOME.get(name, 0))

    elif cmd == "estop":
        estop_latched = True
        stop_motors()
        release_servos()
        send_line(conn, "L,estop: motors stopped, servos released")

    elif cmd == "resume":
        estop_latched = False
        hold_servos()
        send_line(conn, "L,resumed")

    elif cmd == "thr":
        try:
            tracker_thresh = int(args[0])
            send_line(conn, "L,tracker threshold = %d" % tracker_thresh)
        except Exception:
            send_line(conn, "E,bad threshold")

    elif cmd == "speed":
        try:
            speed = max(0, min(100, int(float(args[0]))))
            send_line(conn, "L,speed = %d" % speed)
        except Exception:
            send_line(conn, "E,bad speed")

    elif cmd == "ping":
        send_line(conn, "P,%d" % _ticks_ms())

    elif cmd == "tel":
        pass                                   # telemetry is sent on its own timer

    else:
        send_line(conn, "E,unknown command: %s" % line)


# ==================== link loop ====================
def serve(conn, sonar):
    """Talk to the GUI until the link breaks."""
    global move_dir, last_move_ms
    buf = b""
    now = _ticks_ms()
    last_telemetry = now - TELEMETRY_MS
    last_wifi_check = now
    last_move_ms = now
    last_rx_ms = now
    empty_recvs = 0
    recv_errs = 0

    try:
        ip = NIC.ifconfig()[0]
    except Exception:
        ip = "?"
    send_line(conn, "L,hello ip=%s fw=%s sonar=%s servos=%s grab=%d open=%d" % (
        ip, ESP32_FW, sonar.pins, SERVO_PINS, GRAB_DEG, OPEN_DEG))
    print("remote_bot: link up, streaming telemetry")

    while True:
        now = _ticks_ms()

        # ---- receive commands (short timeout keeps the loop ticking) ----
        try:
            data = conn.recv(256)
            recv_errs = 0
        except OSError as e:
            if is_idle_error(e):
                data = None
                recv_errs = 0
            else:
                recv_errs += 1
                data = None
                if recv_errs <= 3 or recv_errs % 50 == 0:
                    print("remote_bot: recv error #%d: %r" % (recv_errs, e))
                if recv_errs > 150:
                    print("remote_bot: too many recv errors, reconnecting")
                    break

        if data is not None:
            if data == b"":
                # firmware-dependent: either "peer closed" or "nothing to read".
                # Only give up if it stays empty while the GUI also went silent.
                empty_recvs += 1
                if empty_recvs > 20 and _ticks_diff(now, last_rx_ms) > LINK_TIMEOUT_MS:
                    print("remote_bot: connection looks closed")
                    break
            else:
                empty_recvs = 0
                last_rx_ms = now
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode().strip()
                    if text:
                        handle_command(conn, text)
                if len(buf) > 1024:                # garbage guard
                    buf = buf[-1024:]

        # ---- watchdog: a lost link must never leave the wheels running ----
        if move_dir is not None and _ticks_diff(now, last_move_ms) > WATCHDOG_MS:
            stop_motors()
            print("remote_bot: watchdog stop (no 'move' for %d ms)" % WATCHDOG_MS)
            try:
                send_line(conn, "L,watchdog stop")
            except Exception:
                pass

        # ---- optional: let the arm go loose while the wheels are running ----
        if RELEASE_SERVOS_WHILE_DRIVING and not estop_latched:
            if move_dir is not None and not servos_released:
                release_servos()
                print("remote_bot: driving - servos released")
            elif move_dir is None and servos_released:
                hold_servos()
                print("remote_bot: stopped - servos holding again")

        # ---- wheel + servo easing ----
        service_motors()
        service_servos()

        # ---- telemetry (also proves to ourselves that the link still works) ----
        if _ticks_diff(now, last_telemetry) >= TELEMETRY_MS:
            last_telemetry = now
            flags = 0                              # bit0 estop, bit1 sonar, bit2 tracker, bit3 wifi
            dist = -1
            if sonar.trig is not None:
                flags |= 2                         # ultrasonic sensor detected
                d = sonar.ping()
                if d is not None:
                    dist = int(d)
            adc = (-1, -1, -1, -1, -1)
            onmask = 0
            try:
                adc = NIC.adc((0, 1, 2, 3, 4))
                flags |= 4
                for ch in range(4):
                    if adc[ch] < tracker_thresh:
                        onmask |= (1 << ch)
            except Exception:
                pass
            if estop_latched:
                flags |= 1
            if NIC.isconnected():
                flags |= 8
            vals = (_ticks_ms(), dist,
                    adc[0], adc[1], adc[2], adc[3], adc[4], onmask,
                    SERVO["big_arm"]["angle10"] // 10, SERVO["little_arm"]["angle10"] // 10,
                    SERVO["rotation"]["angle10"] // 10, SERVO["clamp"]["angle10"] // 10,
                    flags)
            send_line(conn, "T," + ",".join([str(v) for v in vals]))

        # ---- link liveness: the GUI pings every ~1.5 s ----
        if _ticks_diff(now, last_rx_ms) > LINK_TIMEOUT_MS:
            print("remote_bot: no data from the GUI for %d ms, reconnecting" % LINK_TIMEOUT_MS)
            break

        # ---- keep an eye on the WiFi ----
        if _ticks_diff(now, last_wifi_check) > 1000:
            last_wifi_check = now
            if not NIC.isconnected():
                print("remote_bot: WiFi lost")
                break

        time.sleep_ms(LOOP_TICK_MS)


def main():
    setup_motors()
    setup_servos()
    print("remote_bot: wheels still, servos easing to home %s" % (SERVO_HOME,))
    for name in SERVO_ORDER:
        servo_target(name, SERVO_HOME.get(name, 0))
    for _ in range(40):                 # ease to the home pose before going online
        service_servos()
        time.sleep_ms(20)
    hold_servos()                       # a servo already at its home (big arm: 0)
                                        # never got a duty write - give it one so it
                                        # holds instead of hanging loose

    sonar = Sonar()
    sonar.detect()

    if not esp32_open():
        pass                     # keep going: wifi_connect() retries the ESP32 too
    while True:
        wifi_connect()
        conn = None
        try:
            print("remote_bot: connecting to %s:%d ..." % (SERVER_HOST, SERVER_PORT))
            conn = tcp_connect()
            print("remote_bot: connected to the GUI")
            serve(conn, sonar)
        except Exception as e:
            print("remote_bot: link failed: %r" % (e,))
        finally:
            stop_motors()
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            gc.collect()
        print("remote_bot: retrying in 2 s ...")
        time.sleep(2)


try:
    main()
except KeyboardInterrupt:
    print("remote_bot: stopped by user")
finally:
    try:
        stop_motors()
        release_servos()
    except Exception:
        pass
    print("remote_bot: done")
