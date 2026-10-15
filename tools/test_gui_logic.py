"""Deterministic test of the GUI's input logic (pad, keys, repeat, focus) with
synthetic Tk events - no compositor / XTest involvement.

Run:  python3 tools/test_gui_logic.py       (needs a display; no cart needed)
"""
import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import remote_gui as rg                                            # noqa: E402

fails = []


def check(cond, what):
    print(("  OK   " if cond else "  FAIL ") + what)
    if not cond:
        fails.append(what)


root = tk.Tk()
app = rg.App(root, {"port": 6150, "log_keys": False})
sent = []
app.link.send = lambda cmd: sent.append(cmd)          # observe instead of sending
type(app.link).connected = property(lambda self: True)   # pretend a bot is connected


def settle(seconds):
    t0 = time.time()
    while time.time() - t0 < seconds:
        root.update()
        time.sleep(0.01)


def press(k):
    root.event_generate("<KeyPress>", keysym=k, when="now")


def release(k):
    root.event_generate("<KeyRelease>", keysym=k, when="now")


# ---- mouse pad: press and hold forward, then release -------------------------
sent[:] = []
app._pad_press("f")
settle(0.6)
fwd = [c for c in sent if c == "move f 55"]
app._pad_release("f")
settle(0.4)
check(len(fwd) >= 3, "pad hold sends repeated 'move f 55' (%d times)" % len(fwd))
check(sent[-1] == "stop", "pad release ends with 'stop' (last: %r)" % sent[-1])
check("stop" not in sent[:-1], "no 'stop' while the pad button is held")

# ---- mouse pad: spin right ---------------------------------------------------
sent[:] = []
app._pad_press("tr")
settle(0.4)
app._pad_release("tr")
settle(0.3)
check(any(c == "move tr 55" for c in sent), "pad spin-right sends 'move tr'")

# ---- mouse pad STOP ----------------------------------------------------------
sent[:] = []
app._pad_press("f")
settle(0.3)
app._pad_press("stop")
settle(0.3)
check("stop" in sent, "pad STOP sends 'stop'")
check(app._pad_down == set(), "pad STOP clears the held buttons")

# ---- keyboard: press, auto-repeat pairs, release ------------------------------
sent[:] = []
press("w")
settle(0.35)
n_after_press = len([c for c in sent if c == "move f 55"])
for _ in range(8):                     # Wayland/X11 auto-repeat = release+press
    release("w")
    press("w")
    settle(0.05)
app._pad_press("stop")                 # stop driving before checking
settle(0.3)
moves = [c for c in sent if c == "move f 55"]
check(n_after_press >= 2, "key press drives (%d moves before repeats)" % n_after_press)
check(len(moves) >= n_after_press + 1, "held key keeps driving through repeats (%d)" % len(moves))
check(not any(c == "stop" for c in moves), "no 'stop' during the auto-repeat stream")

# ---- keyboard: release really stops ------------------------------------------
sent[:] = []
press("s")
settle(0.3)
release("s")
settle(0.4)
check("stop" in sent, "key release sends 'stop'")
check(sent[-1] == "stop", "driving ends with 'stop' after the release (tail %r)" % sent[-2:])

# ---- keyboard: servo keys, estop, home ---------------------------------------
sent[:] = []
press("r")
release("r")
settle(0.1)
press("8")
release("8")
settle(0.1)
press("p")
release("p")
settle(0.1)
check("nudge big_arm 5" in sent, "key r nudges the big arm (%r)" % sent[:3])
check("grab" in sent, "key 8 grabs")
check("home" in sent, "key p goes home")

sent[:] = []
press("Escape")
release("Escape")
settle(0.2)
check("estop" in sent, "Escape = estop")

# ---- wheel test buttons ------------------------------------------------------
sent[:] = []
app._wheel_press("D", True)
settle(0.4)
app._wheel_release("D")
settle(0.3)
check(any(c == "motor D f 55" for c in sent), "wheel button sends 'motor D f' (%r)" % sent[:4])
check(any(c == "motor D stop" for c in sent), "wheel button release stops that wheel")
check(not any(c.startswith("move ") for c in sent), "wheel test does not send movement commands")

sent[:] = []
app._wheel_press("A", False)
settle(0.3)
app._wheel_release("A")
settle(0.2)
check(any(c == "motor A r 55" for c in sent), "reverse wheel button sends 'motor A r'")

# ---- steering: horizontal input steers while driving, strafes when stopped ----
sent[:] = []
press("w")
settle(0.15)
press("a")
settle(0.15)
check(any(c == "mix 100 0 -100 55" for c in sent),
      "W+A steers left while driving (%r)" % sent[-3:])
release("a")
settle(0.25)
check(any(c == "move f 55" for c in sent), "releasing A falls back to 'move f'")
press("q")
settle(0.15)
check(any(c == "mix 100 0 -100 55" for c in sent), "W+Q steers left too (spin)")
release("q")
release("w")
settle(0.3)

sent[:] = []
press("a")
settle(0.25)
check(any(c == "move l 55" for c in sent),
      "A alone moves sideways, no forward vector (%r)" % sent[:2])
release("a")
settle(0.25)

sent[:] = []
press("w")
settle(0.15)
press("s")
settle(0.15)
check(any(c == "stop" for c in sent), "W+S cancels out -> 'stop'")
release("s")
release("w")
settle(0.3)

# ---- fast brake: the checkbox drives the bot's firmware state -----------------
sent[:] = []
app.fast_brake.set(True)
app._brake_toggled()
settle(0.05)
check("brake 1" in sent, "fast-brake checkbox engages it (%r)" % sent[:2])
app.fast_brake.set(False)
app._brake_toggled()
settle(0.05)
check("brake 0" in sent, "unchecking disengages it")
sent[:] = []
app.fast_brake.set(True)
app._handle_line("L,hello ip=10.0.0.9 fw=1 sonar=(31, 30) servos={} grab=40 open=-35")
settle(0.05)
check("brake 1" in sent, "the state is re-sent when the bot connects")

# ---- line following: the button, the mirror checkbox and the telemetry --------
sent[:] = []
app._following = False
app.follow_speed.set(45)
app.follow_flip.set(False)
app._follow_toggled()
settle(0.05)
check("follow 1 45 0" in sent, "Follow sends the speed + sensor order (%r)" % sent[:2])
sent[:] = []
app._following = True                       # the bot says it is driving itself
app._follow_toggled()
settle(0.05)
check("follow 0" in sent, "the button stops it once the bot reports following")
sent[:] = []
app.follow_flip.set(True)
app._follow_flip_toggled()
settle(0.05)
check("follow 1 45 1" in sent, "mirroring while running re-sends the settings (%r)" % sent[:2])
app._following = False

tel = "T,1000,55,4095,850,850,4095,0,6,0,0,0,-35,%d,%d,%d" % (4 | 32, 25, 47)
app._handle_line(tel)
settle(0.05)
check(app._following and app.follow_btn.cget("text") == "Stop follow",
      "telemetry flag bit5 flips the button (%r)" % app.follow_btn.cget("text"))
label = app.line_label.cget("text")
check("follow:" in label and "+0.25" in label and "47 Hz" in label,
      "the tracker panel shows the live offset + read rate (%r)" % label)
app._handle_line("T,1100,55,4095,850,850,4095,0,6,0,0,0,-35,%d,%d,%d" % (4, 0, 0))
settle(0.05)
check(app._following is False and app.follow_btn.cget("text") == "Follow line",
      "and it goes back when the bot stops following")

# ---- typing mode: keys are ignored while a text field has focus ---------------
sent[:] = []
app.root.focus_force()
entry = app._field(tk.Entry(app.root))
entry.pack()
entry.focus_set()
root.update()
press("w")
settle(0.3)
check(not any(c.startswith("move") for c in sent), "driving keys ignored while typing in a field")
entry.destroy()
app._take_focus()

# ---- losing the window stops the cart ----------------------------------------
sent[:] = []
press("w")
settle(0.25)
root.event_generate("<FocusOut>", when="now")
settle(0.3)
check("stop" in sent, "FocusOut (window loses focus) sends 'stop'")

# ---- laptop AP button re-points the WiFi fields at the hotspot ----------------
class _FakeRun:
    returncode = 0
    stdout = "laptop AP 'MaixCart-AP' up on wlp3s0 (2.4 GHz)\n  this laptop: 10.42.0.1"
    stderr = ""


_real_run = rg.subprocess.run
rg.subprocess.run = lambda *a, **kw: _FakeRun()
try:
    app._ap_worker("start")
    settle(0.2)
finally:
    rg.subprocess.run = _real_run
check(app.ssid.get() == rg.AP_SSID and app.password.get() == rg.AP_PASS,
      "AP start fills in the hotspot SSID/password (%r/%r)" % (app.ssid.get(), app.password.get()))
check(app.ap_btn.cget("text") == "Stop AP", "the button turns into 'Stop AP'")
check("laptop AP is up" in app.logbox.get("1.0", "end"),
      "the log tells you to press Upload next")

print()
print("FAILED: %d" % len(fails))
for f in fails:
    print("  - " + f)
app.link.shutdown()
root.destroy()
sys.exit(1 if fails else 0)
