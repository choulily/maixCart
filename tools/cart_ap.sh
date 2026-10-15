#!/bin/sh
# cart_ap.sh - start/stop a 2.4 GHz WiFi access point on this laptop, so the
# cart's ESP32 connects to the laptop *directly* instead of going through a
# phone hotspot (one hop less, no phone in the path).
#
#   tools/cart_ap.sh start     # bring the AP up (SSID/password below)
#   tools/cart_ap.sh stop      # tear it down
#   tools/cart_ap.sh status    # is it running?
#
# After 'start' the GUI must be told the new WiFi: the GUI's "Laptop AP"
# button does all of this in one click (starts the AP, fills its SSID/password
# and the laptop's address into the fields, then tells you to press
# "Upload & run on cart").  Without the GUI:
#   CART_AP_SSID=... CART_AP_PASS=... tools/cart_ap.sh start
#   python3 tools/upload_bot.py --ssid <SSID> --pass <PASS> --host 10.42.0.1
#
# Note: while the AP runs on the only WiFi card the laptop cannot also be a
# client of the phone hotspot - it has no internet unless Ethernet is plugged
# in (or you use a second WiFi adapter).
set -e
SSID="${CART_AP_SSID:-MaixCart-AP}"
PASS="${CART_AP_PASS:-maixcart123}"
CONN="maixcart-ap"
IFACE="$(nmcli -t -f DEVICE,TYPE device status | awk -F: '$2=="wifi"{print $1; exit}')"
[ -z "$IFACE" ] && { echo "no wifi interface found"; exit 1; }

case "${1:-status}" in
  start)
    nmcli connection down "$CONN" >/dev/null 2>&1 || true
    nmcli connection delete "$CONN" >/dev/null 2>&1 || true
    nmcli device wifi hotspot con-name "$CONN" ifname "$IFACE" \
          ssid "$SSID" password "$PASS" band bg
    echo "laptop AP '$SSID' up on $IFACE (2.4 GHz), password '$PASS'"
    ip -4 -o addr show "$IFACE" | awk '{print "  this laptop (bot connects to): " $4}'
    ;;
  stop)
    if nmcli connection down "$CONN" >/dev/null 2>&1; then
      echo "laptop AP stopped"
    else
      echo "laptop AP was not running"
    fi
    nmcli connection delete "$CONN" >/dev/null 2>&1 || true
    ;;
  *)
    if nmcli -t -f NAME,DEVICE connection show --active | grep -q "^$CONN:"; then
      echo "laptop AP is running ($SSID)"
    else
      echo "laptop AP is not running"
    fi
    ;;
esac
