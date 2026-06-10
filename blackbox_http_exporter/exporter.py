"""
Blackbox HTTP Prometheus Exporter
Probes a list of HTTP/HTTPS endpoints: up/down, response time,
status code, redirect count, content match, and SSL expiry.
"""

import ssl
import time
import socket
import logging
import re
import urllib.request
import urllib.error
import concurrent.futures
from datetime import datetime, timezone
import yaml
from prometheus_client import start_http_server, Gauge, Histogram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Metrics ───────────────────────────────────────────────────────────────────
PROBE_SUCCESS      = Gauge("probe_success",                    "1 if probe succeeded",          ["target", "name"])
PROBE_DURATION     = Gauge("probe_duration_seconds",           "Probe response time",            ["target", "name"])
PROBE_STATUS_CODE  = Gauge("probe_http_status_code",           "HTTP response status code",      ["target", "name"])
PROBE_REDIRECTS    = Gauge("probe_http_redirects",             "Number of HTTP redirects",       ["target", "name"])
PROBE_SSL_EXPIRY   = Gauge("probe_ssl_expiry_days",            "Days until SSL cert expires",    ["target", "name"])
PROBE_SSL_VALID    = Gauge("probe_ssl_valid",                  "1 if SSL cert is valid",         ["target", "name"])
PROBE_CONTENT_MATCH= Gauge("probe_http_content_match",         "1 if content regex matched",     ["target", "name"])
PROBE_DNS_DURATION = Gauge("probe_dns_lookup_duration_seconds","DNS resolution time",            ["target", "name"])
PROBE_CONNECT_TIME = Gauge("probe_tcp_connect_duration_seconds","TCP connect time",              ["target", "name"])

def check_ssl_expiry(hostname: str, port: int = 443) -> tuple:
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=5) as raw:
            with ctx.wrap_socket(raw, server_hostname=hostname) as tls:
                cert = tls.getpeercert()
        expiry = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days = (expiry - datetime.now(timezone.utc)).days
        return days, True
    except ssl.SSLCertVerificationError:
        return -1, False
    except Exception:
        return -1, True  # port not SSL

def probe(target: dict):
    url      = target["url"]
    name     = target.get("name", url)
    method   = target.get("method", "GET").upper()
    timeout  = target.get("timeout", 10)
    expected = target.get("expected_status", 200)
    regex    = target.get("content_match", "")
    follow   = target.get("follow_redirects", True)
    labels   = {"target": url, "name": name}

    dns_start = time.time()
    try:
        parsed = urllib.parse.urlparse(url) if hasattr(urllib, 'parse') else None
        hostname = url.split("//")[1].split("/")[0].split(":")[0]
        socket.getaddrinfo(hostname, None)
        dns_end = time.time()
        PROBE_DNS_DURATION.labels(**labels).set(round(dns_end - dns_start, 4))
    except Exception:
        pass

    start = time.time()
    try:
        import urllib.parse
        req = urllib.request.Request(url, method=method)
        req.add_header("User-Agent", "prometheus-blackbox-exporter/1.0")

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if not target.get("verify_ssl", True):
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        handler = urllib.request.HTTPSHandler(context=ctx) if url.startswith("https") else urllib.request.HTTPHandler()
        redirect_count = [0]

        class RedirectCounter(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                redirect_count[0] += 1
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        opener = urllib.request.build_opener(handler, RedirectCounter())
        if not follow:
            opener = urllib.request.build_opener(handler)

        with opener.open(req, timeout=timeout) as resp:
            status = resp.status
            body   = resp.read().decode(errors="replace")

        duration = time.time() - start
        success  = 1 if status == expected else 0

        PROBE_SUCCESS.labels(**labels).set(success)
        PROBE_DURATION.labels(**labels).set(round(duration, 4))
        PROBE_STATUS_CODE.labels(**labels).set(status)
        PROBE_REDIRECTS.labels(**labels).set(redirect_count[0])

        if regex:
            match = 1 if re.search(regex, body) else 0
            PROBE_CONTENT_MATCH.labels(**labels).set(match)

        if url.startswith("https://"):
            hostname = url.split("//")[1].split("/")[0]
            days, valid = check_ssl_expiry(hostname)
            PROBE_SSL_EXPIRY.labels(**labels).set(days)
            PROBE_SSL_VALID.labels(**labels).set(1 if valid else 0)

        log.info("%-50s status=%-3d time=%.3fs", url, status, duration)

    except urllib.error.HTTPError as e:
        duration = time.time() - start
        PROBE_SUCCESS.labels(**labels).set(0)
        PROBE_STATUS_CODE.labels(**labels).set(e.code)
        PROBE_DURATION.labels(**labels).set(round(duration, 4))
        log.warning("HTTP error %s — %s", url, e.code)
    except Exception as e:
        duration = time.time() - start
        PROBE_SUCCESS.labels(**labels).set(0)
        PROBE_DURATION.labels(**labels).set(round(duration, 4))
        log.warning("Probe failed %s — %s", url, e)

def main():
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    port     = cfg.get("listen_port", 9700)
    interval = cfg.get("scrape_interval", 60)
    targets  = cfg.get("targets", [])
    workers  = cfg.get("workers", 20)

    start_http_server(port)
    log.info("Blackbox HTTP exporter started on :%d — %d targets", port, len(targets))

    while True:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(probe, t) for t in targets]
            concurrent.futures.wait(futures)
        time.sleep(interval)

if __name__ == "__main__":
    main()
