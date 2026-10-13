#!/usr/bin/env python3
"""Load a local Python file into the K210 RAM over the serial raw REPL and run it.

The cart's SPIFFS filesystem is write-protected, so the file is sent as a
code block and executed directly (it runs top to bottom, including any call
at the end of the file).

Usage:  python3 run_cart_file.py <file.py> [timeout_s]
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from maix_tool import PORT, BAUD, run_raw
import serial

src = open(sys.argv[1]).read()
timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0

ser = serial.Serial(PORT, BAUD, timeout=0.2)
time.sleep(0.2)
try:
    print("=== %s ===" % os.path.basename(sys.argv[1]))
    print(run_raw(ser, src, timeout=timeout))
finally:
    ser.close()
