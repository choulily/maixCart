#!/usr/bin/env python3
"""Talk to the MaixPy cart over the Sipeed debugger UART (/dev/ttyUSB0).

The cart's SPIFFS filesystem is write-protected on the current firmware,
so code is loaded directly into RAM via the raw REPL instead.

Usage:
  maix_tool.py backup  <remote> <local>     # read a device file to disk
  maix_tool.py run     <remote>             # execute a device file (if writable)
  maix_tool.py exec    "<code>"             # run arbitrary code
"""
import base64, sys, time
import serial

PORT = "/dev/ttyUSB1"
BAUD = 115200


def read_until(ser, marker, timeout=10.0):
    data = b""
    t0 = time.time()
    while not data.endswith(marker):
        chunk = ser.read(256)
        if chunk:
            data += chunk
        elif time.time() - t0 > timeout:
            break
    return data


def enter_raw(ser):
    ser.write(b"\x03\x03")  # Ctrl-C twice: stop anything running
    time.sleep(0.3)
    ser.reset_input_buffer()
    ser.write(b"\x01")      # Ctrl-A: raw REPL
    read_until(ser, b">", 5)


def run_raw(ser, code, timeout=20.0):
    """Send code to the raw REPL in small chunks, execute, return output."""
    enter_raw(ser)
    payload = code.encode()
    for i in range(0, len(payload), 256):     # chunked to avoid overflow
        ser.write(payload[i:i + 256])
        time.sleep(0.05)
    ser.write(b"\x04")                        # execute
    out = read_until(ser, b"\x04>", timeout)
    ser.write(b"\x02")                        # Ctrl-B: friendly REPL again
    text = out.decode(errors="replace")
    if text.endswith("\x04>"):
        text = text[:-2]
    if text.startswith("OK"):
        text = text[2:]
    return text


def read_remote_bytes(ser, remote, timeout=30.0):
    code = ("import ubinascii\n"
            "print('B64:'+ubinascii.b2a_base64(open(%r,'rb').read()).decode())\n"
            % remote)
    out = run_raw(ser, code, timeout)
    for line in out.splitlines():
        if line.startswith("B64:"):
            return base64.b64decode(line[4:])
    raise RuntimeError("no B64 line in response: %r" % out)


def main():
    cmd = sys.argv[1]
    ser = serial.Serial(PORT, BAUD, timeout=0.2)
    time.sleep(0.2)
    try:
        if cmd == "backup":
            data = read_remote_bytes(ser, sys.argv[2])
            open(sys.argv[3], "wb").write(data)
            print("backed up %s -> %s (%d bytes)" % (sys.argv[2], sys.argv[3], len(data)))
        elif cmd == "run":
            out = run_raw(ser, "exec(open(%r).read())" % sys.argv[2], timeout=120.0)
            print(out)
        elif cmd == "exec":
            print(run_raw(ser, sys.argv[2], timeout=120.0))
        else:
            sys.exit(__doc__)
    finally:
        ser.close()


if __name__ == "__main__":
    main()
