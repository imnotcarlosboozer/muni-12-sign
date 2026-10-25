#!/usr/bin/env python3
"""Show the weather and next Muni 12 Pacific arrivals at Pacific Ave & Taylor St on HUB75 LED panels.

Run on the Pi:     sudo python3 muni_sign.py
Preview anywhere:  python3 muni_sign.py --preview

Predictions come from the 511.org StopMonitoring API. Put your free key
(https://511.org/open-data/token) in api_key.txt next to this script.
Weather comes from Open-Meteo, which needs no key.
"""
import argparse
import datetime
import gzip
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
API_KEY_PATH = os.path.join(APP_DIR, "api_key.txt")
FONT_DIR = os.path.join(APP_DIR, "fonts")

STOP_MONITORING_URL = "https://api.511.org/transit/StopMonitoring"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
STALE_AFTER_SECS = 10 * 60  # stop showing predictions this old
WEATHER_STALE_AFTER_SECS = 60 * 60
RETRY_SECS = 30
DEGREE = "\u00b0"

WHITE = (255, 255, 255)
GRAY = (130, 130, 130)
TEAL = (0, 180, 200)
YELLOW = (255, 200, 0)
GREEN = (0, 220, 0)


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_api_key():
    key = os.environ.get("MUNI_511_KEY", "").strip()
    if not key and os.path.exists(API_KEY_PATH):
        with open(API_KEY_PATH) as f:
            key = f.read().strip()
    if not key:
        sys.exit(f"No 511 API key. Put it in {API_KEY_PATH} (get one at https://511.org/open-data/token)")
    return key


def parse_time(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def fetch_arrivals(api_key, agency, stop_code, line):
    """Return sorted arrival times (epoch secs) for one line at one stop."""
    query = urllib.parse.urlencode({
        "api_key": api_key, "agency": agency, "stopcode": stop_code, "format": "json",
    })
    with urllib.request.urlopen(f"{STOP_MONITORING_URL}?{query}", timeout=20) as resp:
        body = resp.read()
    # 511 sometimes gzips the reply unasked, and prefixes its JSON with a byte order mark.
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    data = json.loads(body.decode("utf-8-sig"))
    visits = data["ServiceDelivery"]["StopMonitoringDelivery"].get("MonitoredStopVisit") or []
    arrivals = []
    for visit in visits:
        journey = visit["MonitoredVehicleJourney"]
        if journey.get("LineRef") != line:
            continue
        call = journey.get("MonitoredCall") or {}
        when = call.get("ExpectedArrivalTime") or call.get("AimedArrivalTime")
        if when:
            arrivals.append(parse_time(when))
    return sorted(arrivals)


class ArrivalPoller:
    """Polls 511 in a background thread so the display never waits on the network."""

    def __init__(self, api_key, transit_cfg):
        self.api_key = api_key
        self.cfg = transit_cfg
        self.lock = threading.Lock()
        self.results = {}  # stop_code -> (fetched_at, [arrival epochs])

    def poll_once(self):
        ok = True
        for stop in self.cfg["stops"]:
            try:
                arrivals = fetch_arrivals(self.api_key, self.cfg["agency"], stop["stop_code"], self.cfg["line"])
            except Exception as e:
                print(f"511 fetch failed for stop {stop['stop_code']}: {e}", file=sys.stderr, flush=True)
                ok = False
                continue
            with self.lock:
                self.results[stop["stop_code"]] = (time.time(), arrivals)
        return ok

    def run(self):
        while True:
            ok = self.poll_once()
            time.sleep(self.cfg["poll_seconds"] if ok else RETRY_SECS)

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def get(self, stop_code):
        with self.lock:
            return self.results.get(stop_code)


def weather_condition(code, is_day):
    """Short label for a WMO weather code (as used by Open-Meteo)."""
    if code in (0, 1):
        return "SUNNY" if is_day else "CLEAR"
    if code == 2:
        return "P CLOUDY"
    if code == 3:
        return "CLOUDY"
    if code in (45, 48):
        return "FOG"
    if 51 <= code <= 57:
        return "DRIZZLE"
    if 61 <= code <= 67:
        return "RAIN"
    if 80 <= code <= 82:
        return "SHOWERS"
    if 71 <= code <= 77 or code in (85, 86):
        return "SNOW"
    if code >= 95:
        return "STORM"
    return ""


def fetch_weather(weather_cfg):
    query = urllib.parse.urlencode({
        "latitude": weather_cfg["latitude"],
        "longitude": weather_cfg["longitude"],
        "current": "temperature_2m,apparent_temperature,weather_code,is_day",
        "temperature_unit": "fahrenheit",
    })
    with urllib.request.urlopen(f"{OPEN_METEO_URL}?{query}", timeout=20) as resp:
        current = json.load(resp)["current"]
    return {
        "temp": round(current["temperature_2m"]),
        "feels": round(current["apparent_temperature"]),
        "condition": weather_condition(current["weather_code"], current.get("is_day", 1)),
    }


class WeatherPoller:
    def __init__(self, weather_cfg):
        self.cfg = weather_cfg
        self.result = None  # (fetched_at, weather dict)

    def poll_once(self):
        try:
            self.result = (time.time(), fetch_weather(self.cfg))
            return True
        except Exception as e:
            print(f"Weather fetch failed: {e}", file=sys.stderr, flush=True)
            return False

    def run(self):
        while True:
            time.sleep(self.cfg["poll_seconds"] if self.poll_once() else RETRY_SECS)

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def get(self, now):
        if self.result is None or now - self.result[0] > WEATHER_STALE_AFTER_SECS:
            return None
        return self.result[1]


def weather_segments(weather):
    """Left side of the weather row as (text, font, rgb) runs, plus the condition label."""
    if weather is None:
        return [("WEATHER --", "label", GRAY)], ""
    return [
        (f"{weather['temp']}{DEGREE}", "time", WHITE),
        (" FEELS ", "label", GRAY),
        (f"{weather['feels']}{DEGREE}", "time", TEAL),
    ], weather["condition"]


def clock_label(epoch):
    t = time.localtime(epoch)
    return f"{(t.tm_hour % 12) or 12}:{t.tm_min:02d}{'A' if t.tm_hour < 12 else 'P'}"


def arrival_text(result, now, count):
    """Right-hand text for one row (up to `count` arrivals), and its color."""
    if result is None:
        return "...", GRAY
    fetched_at, arrivals = result
    if now - fetched_at > STALE_AFTER_SECS:
        return "--", GRAY
    minutes = [int((a - now) // 60) for a in arrivals if a > now - 30]
    if not minutes:
        return "NO BUSES", GRAY
    if minutes[0] >= 60:
        return clock_label(now + minutes[0] * 60), GRAY
    shown = [m for m in minutes[:count] if m < 60]
    text = ", ".join("NOW" if m <= 0 else str(m) for m in shown) + ("m" if shown[-1] > 0 else "")
    return text, GREEN if shown[0] <= 0 else YELLOW


def row_label(stop, line):
    return [(f"{line} ", WHITE), (f"{stop['direction']} ", TEAL), (stop["destination"], GRAY)]


def run_matrix(cfg, poller, weather_poller):
    from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics

    p = cfg["panel"]
    opts = RGBMatrixOptions()
    opts.rows = p["rows"]
    opts.cols = p["cols"]
    opts.chain_length = p["chain_length"]
    opts.parallel = p["parallel"]
    opts.hardware_mapping = p["hardware_mapping"]
    opts.gpio_slowdown = p["gpio_slowdown"]
    opts.brightness = p["brightness"]
    opts.pwm_bits = p["pwm_bits"]

    # Load fonts before the matrix starts: it then drops root, and can't read a private home dir.
    label_font = graphics.Font()
    label_font.LoadFont(os.path.join(FONT_DIR, cfg["display"]["label_font"]))
    time_font = graphics.Font()
    time_font.LoadFont(os.path.join(FONT_DIR, cfg["display"]["time_font"]))

    matrix = RGBMatrix(options=opts)
    canvas = matrix.CreateFrameCanvas()
    width, height = canvas.width, canvas.height

    colors = {}

    def color(rgb):
        if rgb not in colors:
            colors[rgb] = graphics.Color(*rgb)
        return colors[rgb]

    def text_width(font, text):
        return sum(font.CharacterWidth(ord(c)) for c in text)

    fonts = {"label": label_font, "time": time_font}
    stops = cfg["transit"]["stops"]
    line = cfg["transit"]["line"]
    max_arrivals = cfg["transit"]["max_arrivals"]
    rows = (["weather"] if weather_poller else []) + stops
    tops = [round(i * height / len(rows)) for i in range(len(rows) + 1)]

    def row_baseline(i):
        return tops[i] + (tops[i + 1] - tops[i] - time_font.height) // 2 + time_font.baseline

    while True:
        now = time.time()
        canvas.Clear()
        for i, row in enumerate(rows):
            baseline = row_baseline(i)
            if row == "weather":
                runs, condition = weather_segments(weather_poller.get(now))
                x = 1
                for text, font, rgb in runs:
                    x += graphics.DrawText(canvas, fonts[font], x, baseline, color(rgb), text)
                cond_x = width - text_width(label_font, condition)
                if condition and cond_x >= x + 3:
                    graphics.DrawText(canvas, label_font, cond_x, baseline, color(GRAY), condition)
                continue
            stop = row
            x = 1
            for text, rgb in row_label(stop, line):
                x += graphics.DrawText(canvas, label_font, x, baseline, color(rgb), text)
            label_end = x + 2

            # Show as many arrivals as fit next to the label.
            result = poller.get(stop["stop_code"])
            for count in range(max_arrivals, 0, -1):
                text, rgb = arrival_text(result, now, count)
                if text_width(time_font, text) <= width - label_end:
                    break
            graphics.DrawText(canvas, time_font, width - text_width(time_font, text), baseline, color(rgb), text)
        canvas = matrix.SwapOnVSync(canvas)
        time.sleep(1)


def run_preview(cfg, poller, weather_poller):
    poller.poll_once()
    now = time.time()
    if weather_poller:
        weather_poller.poll_once()
        runs, condition = weather_segments(weather_poller.get(now))
        print(f"{''.join(t for t, _, _ in runs):<16}{condition:>10}")
    line = cfg["transit"]["line"]
    for stop in cfg["transit"]["stops"]:
        label = "".join(t for t, _ in row_label(stop, line))
        text, rgb = arrival_text(poller.get(stop["stop_code"]), now, cfg["transit"]["max_arrivals"])
        print(f"{label:<16}\033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m{text:>10}\033[0m")
    for stop in cfg["transit"]["stops"]:
        result = poller.get(stop["stop_code"])
        if result:
            print(f"  stop {stop['stop_code']}: " + ", ".join(clock_label(a) for a in result[1][:5]))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--preview", action="store_true", help="print arrivals instead of driving the panel")
    args = parser.parse_args()
    cfg = load_config()
    poller = ArrivalPoller(load_api_key(), cfg["transit"])
    weather_cfg = cfg.get("weather")
    weather_poller = WeatherPoller(weather_cfg) if weather_cfg and weather_cfg.get("enabled", True) else None
    if args.preview:
        run_preview(cfg, poller, weather_poller)
        return
    poller.start()
    if weather_poller:
        weather_poller.start()
    try:
        run_matrix(cfg, poller, weather_poller)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
