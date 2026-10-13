#!/usr/bin/env python3
"""Start remote_bot.py on the Maixduino cart over the serial link.

How it works: this cart's SPIFFS flash filesystem is unreliable (writes can
corrupt it - that is why this project has always loaded code into RAM), so the
WiFi settings are patched into the program text and the whole program is sent to
the cart's RAM through the raw REPL and started.  Nothing is written to the
cart's flash, and the cart does not need an SD card.

Usage / 用法:
  python3 tools/upload_bot.py                    # start with the defaults below
  python3 tools/upload_bot.py --ssid MyAP --pass 12345678 --host 10.0.0.5
  python3 tools/upload_bot.py --no-run           # only check the serial link
  python3 tools/upload_bot.py --stop             # reset the cart = stop the bot

The GUI (tools/remote_gui.py) has an "Upload & run on cart" button that does the
same thing, so you normally do not need this script by hand.
"""
import argparse
import os
import re
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from maix_tool import PORT, BAUD                                  # noqa: E402
import serial                                                   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BOT_SRC = os.path.join(HERE, "..", "remote_bot.py")

_serial = None

VIRTUAL_IFACES = ("tun", "tap", "tailscale", "utun", "wg", "docker", "br-", "virbr",
                  "mihomo", "clash", "vmnet", "vbox", "zt")


def _scored_ips():
    """[(score, iface, ip)] for this machine, best first."""
    cands = []
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True,
                             text=True, timeout=3).stdout
        for line in out.splitlines():
            parts = line.split()
            if "inet" not in parts:
                continue
            iface = parts[1]
            ip = parts[parts.index("inet") + 1].split("/")[0]
            if iface == "lo" or ip.startswith("127."):
                continue
            cands.append((iface, ip))
    except Exception:
        cands = []

    def score(item):
        iface, ip = item
        s = 0
        if (ip.startswith("10.") or ip.startswith("192.168.")
                or (ip.startswith("172.") and 16 <= int(ip.split(".")[1]) <= 31)):
            s += 10                       # ordinary home / office LAN address
        if iface.startswith(("wl", "en", "eth")):
            s += 5                        # physical wifi / ethernet: the cart can reach it
        if iface.lower().startswith(VIRTUAL_IFACES):
            s -= 20                       # VPN / proxy / container: the cart cannot reach it
        return s

    return sorted([(score(i), i[0], i[1]) for i in cands], reverse=True)


def lan_ips():
    """All IPv4 addresses of this machine, best candidate first."""
    return [ip for _, _, ip in _scored_ips()]


def preferred_ips():
    """Addresses on a normal LAN that the cart can reach."""
    return [ip for s, _, ip in _scored_ips() if s > 0]


def lan_ip():
    """This machine's address on the local network (the bot connects to it).

    Because a VPN/proxy interface (Tailscale, Mihomo, ...) can steal the default
    route, the address is picked by scoring the interfaces instead of asking the
    kernel for the route source address.
    """
    ips = lan_ips()
    if ips:
        return ips[0]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))        # picks the route, sends nothing
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def patch_config(source, ssid, password, host, port):
    """Return remote_bot.py's text with the cart-side config lines replaced."""
    lines = {
        "WIFI_SSID": repr(str(ssid)),
        "WIFI_PASS": repr(str(password)),
        "SERVER_HOST": repr(str(host)),
        "SERVER_PORT": str(int(port)),
    }
    out = source
    for name, value in lines.items():
        pattern = r"(?m)^%s\s*=.*$" % name
        if not re.search(pattern, out):
            raise RuntimeError("no %s line found in %s" % (name, BOT_SRC))
        out = re.sub(pattern, "%s = %s" % (name, value), out, count=1)
    return out


def compact_source(source):
    """Drop comment-only and blank lines: less RAM and a faster upload."""
    keep = []
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        keep.append(line)
    return "\n".join(keep) + "\n"


def open_serial(port=PORT, baud=BAUD, keep_open=True):
    """Open (or reuse) the Sipeed debugger UART."""
    global _serial
    if keep_open and _serial is not None and _serial.is_open:
        return _serial
    ser = serial.Serial(port, baud, timeout=0.2)
    time.sleep(0.2)
    if keep_open:
        _serial = ser
    return ser


def reset_cart(serial_port=PORT, log=print):
    """Reset the board = stop the running program (Ctrl-C cannot do it: MaixPy
    only reads the serial port while the REPL is waiting, so a running program
    never sees Ctrl-C).  The Sipeed debugger's DTR/RTS lines are wired to the
    K210's reset, so pulsing them reboots the cart.
    """
    global _serial
    ser = open_serial(serial_port)
    try:
        ser.dtr = False
        ser.rts = False
        time.sleep(0.2)
        ser.reset_input_buffer()
        ser.dtr = True
        time.sleep(0.15)
        ser.dtr = False
        time.sleep(0.15)
        ser.rts = True
        time.sleep(0.15)
        ser.rts = False
    except Exception as e:
        log("cannot pulse the reset lines on %s (%s)" % (serial_port, e))
        log("-> press the RST button on the board instead")
        return False
    found = False
    t0 = time.time()
    text = b""
    while time.time() - t0 < 6.0:                # wait for the boot banner / REPL
        chunk = ser.read(4096)
        if chunk:
            text += chunk
            if b"init end" in text or b">>>" in text:
                found = True
                break
        time.sleep(0.05)
    time.sleep(0.3)
    ser.reset_input_buffer()
    log("cart %s" % ("reset (bot stopped, motors off)" if found else
                     "reset sent (no boot banner seen - check the cart)"))
    return True


def stop(serial_port=PORT, log=print):
    """Stop the program running on the cart (alias for reset_cart)."""
    return reset_cart(serial_port, log)


def run_stream(ser, code, log=print, seconds=10.0, chunk=256):
    """Send code to the raw REPL, execute it, and stream its output live.

    (maix_tool.run_raw waits for the program to finish, which never happens for
    a bot program, so the startup messages would all arrive at the end.)
    """
    from maix_tool import enter_raw
    enter_raw(ser)
    payload = code.encode()
    for i in range(0, len(payload), chunk):        # chunked to avoid overflow
        ser.write(payload[i:i + chunk])
        time.sleep(0.05)
    ser.write(b"\x04")                            # execute
    t0 = time.time()
    buf = b""
    first = True
    while time.time() - t0 < seconds:
        data = ser.read(256)
        if data:
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode(errors="replace").rstrip()
                if first and text.startswith("OK"):       # raw REPL's "OK" ack
                    text = text[2:]
                if text and not text.endswith(">>"):      # skip raw-REPL prompts
                    log("  bot| " + text)
                    first = False
        else:
            time.sleep(0.05)
    if buf:
        text = buf.decode(errors="replace").rstrip()
        if text:
            log("  bot| " + text)
    return True


def upload(ssid, password, host, port, run=True, log=print,
           serial_port=PORT, run_timeout=12.0, reset=True):
    """Patch the config, send the program to the cart's RAM and start it."""
    global _serial
    src = compact_source(patch_config(open(BOT_SRC).read(), ssid, password, host, port))
    if reset:
        ser = open_serial(serial_port)
        reset_cart(serial_port, log)
    else:
        ser = open_serial(serial_port)
    log("sending %d bytes of code to the cart on %s ..." % (len(src), serial_port))
    log("  wifi=%r  server=%s:%d" % (ssid, host, port))
    if not run:
        log("  (--no-run: serial link is fine, nothing started)")
        return src
    log("  starting the bot (its startup messages follow) ...")
    run_stream(ser, src, log=log, seconds=run_timeout)
    return src


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ssid", default="SONY Xperia 1 V", help="WiFi name the bot joins (2.4 GHz)")
    ap.add_argument("--pass", dest="password", default="password", help="WiFi password")
    ap.add_argument("--host", default=lan_ip(), help="this laptop's IP (default: detected)")
    ap.add_argument("--port", type=int, default=6000, help="TCP port (default 6000)")
    ap.add_argument("--device", default=PORT, help="serial device (default %s)" % PORT)
    ap.add_argument("--no-run", action="store_true", help="do not start the bot")
    ap.add_argument("--stop", action="store_true", help="stop the program running on the cart")
    args = ap.parse_args()

    if args.stop:
        reset_cart(args.device)
        return
    upload(args.ssid, args.password, args.host, args.port,
           run=not args.no_run, serial_port=args.device)


if __name__ == "__main__":
    main()
