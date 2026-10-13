#!/usr/bin/env python3
"""Run the cart movement demo on the Maixduino cart.

Loads ../cart_move.py into the K210 RAM over the serial raw REPL
(the cart's SPIFFS filesystem is write-protected, so on-device files
cannot be created) and calls its demo() sequence:

  forward / backward / sideways left / sideways right / spin left / spin right

Usage:  python3 run_cart_demo.py
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from maix_tool import PORT, BAUD, run_raw
import serial

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "cart_move.py")

src = open(SRC).read()
head = src.rsplit("\ntry:", 1)[0]   # drop the auto-run tail; keep definitions + demo()

ser = serial.Serial(PORT, BAUD, timeout=0.2)
time.sleep(0.2)
try:
    print("=== loading definitions ===")
    print(run_raw(ser, head, timeout=60))
    print("=== calling demo() ===")
    print(run_raw(ser, "demo()", timeout=120))
finally:
    ser.close()
