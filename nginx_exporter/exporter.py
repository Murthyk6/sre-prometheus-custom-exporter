"""
NGINX Prometheus Exporter
Scrapes NGINX stub_status endpoint + parses access logs for request rate,
error rate, upstream latency, and status code breakdown.
"""

import time
import re
import threading
import logging
from collections import defaultdict, deque
import urllib.request
import yaml
from prometheus_client import start_http_server, Gauge, Counter, Histogram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Metrics ───────────────────────────────────────────────────────────────────
NGINX_UP              = Gauge("nginx_up",                     "NGINX is reachable")
NGINX_ACTIVE_CONN     = Gauge("nginx_connections_active",     "Active connections")
NGINX_READING         = Gauge("nginx_connections_reading",    "Connections reading request")
NGINX_WRITING         = Gauge("nginx_connections_writing",    "Connections writing response")
NGINX_WAITING         = Gauge("nginx_connections_waiting",    "Keep-alive waiting connections")
NGINX_ACCEPTS_TOTAL   = Counter("nginx_connections_accepted_total", "Total accepted connections")
NGINX_HANDLED_TOTAL   = Counter("nginx_connections_handled_total",  "Total handled connections")
NGINX_REQUESTS_TOTAL  = Counter("nginx_http_requests_total",        "Total HTTP requests")
NGINX_STATUS_CODES    = Counter("nginx_http_status_codes_total",    "HTTP responses by status code", ["status_class"])
NGINX_REQUEST_LATENCY = Histogram("nginx_request_duration_seconds", "Request duration from access log",
                                   buckets=[.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5])
NGINX_UPSTREAM_LATENCY= Histogram("nginx_upstream_response_duration_seconds", "Upstream response time",
                                   buckets=[.005, .01, .025, .05, .1, .25, .5, 1, 2.5])
NGINX_ERROR_RATE      = Gauge("nginx_error_rate_5xx_percent", "Percentage of 5xx responses in last minute")

# ── Stub Status Scraper ───────────────────────────────────────────────────────
_prev_accepts  = 0
_prev_handled  = 0
_prev_requests = 0

def scrape_stub_status(url: str):
    global _prev_accepts, _prev_handled, _prev_requests
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            body = r.read().decode()
        NGINX_UP.set(1)

        active  = re.search(r"Active connections:\s+(\d+)", body)
        accepts = re.search(r"^\s+(\d+)\s+(\d+)\s+(\d+)", body, re.MULTILINE)
        rw      = re.search(r"Reading:\s+(\d+)\s+Writing:\s+(\d+)\s+Waiting:\s+(\d+)", body)

        if active:
            NGINX_ACTIVE_CONN.set(int(active.group(1)))
        if accepts:
            acc, hdl, req = int(accepts.group(1)), int(accepts.group(2)), int(accepts.group(3))
            if acc > _prev_accepts:
                NGINX_ACCEPTS_TOTAL.inc(acc - _prev_accepts)
            if hdl > _prev_handled:
                NGINX_HANDLED_TOTAL.inc(hdl - _prev_handled)
            if req > _prev_requests:
                NGINX_REQUESTS_TOTAL.inc(req - _prev_requests)
            _prev_accepts, _prev_handled, _prev_requests = acc, hdl, req
        if rw:
            NGINX_READING.set(int(rw.group(1)))
            NGINX_WRITING.set(int(rw.group(2)))
            NGINX_WAITING.set(int(rw.group(3)))

    except Exception as e:
        NGINX_UP.set(0)
        log.warning("stub_status scrape failed: %s", e)

# ── Access Log Parser ─────────────────────────────────────────────────────────
# Default combined log format + $request_time $upstream_response_time
LOG_RE = re.compile(
    r'(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] '
    r'"(?P<method>\S+) (?P<path>\S+) \S+" '
    r'(?P<status>\d{3}) \d+ "[^"]*" "[^"]*"'
    r'(?:\s+(?P<req_time>[\d.]+))?'
    r'(?:\s+(?P<ups_time>[\d.-]+))?'
)

_recent_statuses: deque = deque(maxlen=1000)

def parse_access_log_line(line: str):
    m = LOG_RE.search(line)
    if not m:
        return
    status = m.group("status")
    status_class = status[0] + "xx"
    NGINX_STATUS_CODES.labels(status_class=status_class).inc()
    _recent_statuses.append(int(status))

    if m.group("req_time"):
        try:
            NGINX_REQUEST_LATENCY.observe(float(m.group("req_time")))
        except ValueError:
            pass

    if m.group("ups_time") and m.group("ups_time") != "-":
        try:
            NGINX_UPSTREAM_LATENCY.observe(float(m.group("ups_time")))
        except ValueError:
            pass

def compute_error_rate():
    while True:
        time.sleep(15)
        if _recent_statuses:
            pct_5xx = sum(1 for s in _recent_statuses if s >= 500) / len(_recent_statuses) * 100
            NGINX_ERROR_RATE.set(round(pct_5xx, 2))

def tail_access_log(path: str):
    log.info("Tailing access log: %s", path)
    try:
        with open(path) as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    parse_access_log_line(line)
                else:
                    time.sleep(0.05)
    except FileNotFoundError:
        log.warning("Access log not found: %s", path)

def main():
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    port       = cfg.get("listen_port", 9113)
    status_url = cfg.get("stub_status_url", "http://localhost/nginx_status")
    access_log = cfg.get("access_log", "")
    interval   = cfg.get("scrape_interval", 15)

    start_http_server(port)
    log.info("NGINX exporter started on :%d", port)

    if access_log:
        threading.Thread(target=tail_access_log, args=(access_log,), daemon=True).start()
        threading.Thread(target=compute_error_rate, daemon=True).start()

    while True:
        scrape_stub_status(status_url)
        time.sleep(interval)

if __name__ == "__main__":
    main()
