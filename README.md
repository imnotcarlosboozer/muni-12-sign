# Muni 12 Pacific LED Sign

Shows the current weather and the next inbound and outbound Muni **12 Folsom/Pacific** arrivals at
**Pacific Ave & Taylor St** on two chained 64x32 HUB75 panels (128x32), driven by a
Raspberry Pi with an Adafruit RGB Matrix Bonnet.

    60° FEELS 57°        CLOUDY
    12 IN NOB HILL       4, 12m
    12 OUT MISSION       7, 19m

Times are minutes away (green `NOW` when a bus is due). A bus more than an hour out
shows its clock time; `NO BUSES` when 511 has no predictions (the 12 runs about
6 AM to 10 PM); `--` if predictions are more than 10 minutes old.

## Setup
1. Get a free 511 API key: https://511.org/open-data/token
2. Copy this folder to the Pi and save the key as `api_key.txt` in it.
3. On the Pi: `sudo ./install.sh` (needs the rpi-rgb-led-matrix Python bindings installed).
   This also disables the `sleeper-ticker` service if present, since only one
   program can drive the panels.

Preview without the panels: `python3 muni_sign.py --preview`
Logs: `journalctl -u muni-sign -f`

## config.json
- `transit.stops`: stop codes. 15856 = inbound (westbound on Pacific, to Nob Hill),
  15857 = outbound (eastbound, to the Mission). Find others on sfmta.com/routes.
- `transit.poll_seconds`: 150 checks both stops 48 times an hour, under 511's
  default limit of 60 requests per hour per key.
- `transit.max_arrivals`: how many upcoming buses per row (fewer if they don't fit).
- `weather`: Open-Meteo (free, no key) for the given latitude/longitude, checked every
  `poll_seconds` (600). Shows temperature, feels-like, and conditions; `WEATHER --` if
  it hasn't updated in an hour. Set `enabled` to false to go back to two bus rows
  (and set `display.time_font` back to `7x13B.bdf` for bigger times).

## Switching back to the injury ticker
    sudo systemctl disable --now muni-sign
    sudo systemctl enable --now sleeper-ticker
