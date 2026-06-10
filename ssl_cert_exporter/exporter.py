"""
SSL Certificate Prometheus Exporter
Checks SSL certificate expiry for a list of domains/endpoints.
Exports days until expiry, validity status, and issuer info.
"""

import ssl
import socket
import time
import logging
import concurrent.futures
from datetime import datetime, timezone
import yaml
from prometheus_client import start_http_server, Gauge

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Metrics ───────────────────────────────────────────────────────────────────
SSL_CERT_EXPIRY_DAYS  = Gauge("ssl_cert_expiry_days",     "Days until SSL cert expires",          ["domain", "port"])
SSL_CERT_VALID        = Gauge("ssl_cert_valid",            "1 if cert is valid and reachable",     ["domain", "port"])
SSL_CERT_START_TS     = Gauge("ssl_cert_not_before_timestamp", "Cert valid-from Unix timestamp",   ["domain", "port"])
SSL_CERT_EXPIRY_TS    = Gauge("ssl_cert_not_after_timestamp",  "Cert expiry Unix timestamp",       ["domain", "port"])
SSL_PROBE_DURATION    = Gauge("ssl_probe_duration_seconds","Time taken to probe endpoint",         ["domain", "port"])

def check_cert(domain: str, port: int = 443, timeout: int = 10):
    start = time.time()
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((domain, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=domain) as tls:
                cert = tls.getpeercert()

        not_after_str  = cert["notAfter"]
        not_before_str = cert["notBefore"]
        expiry  = datetime.strptime(not_after_str,  "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        start_d = datetime.strptime(not_before_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        now     = datetime.now(timezone.utc)
        days    = (expiry - now).days

        SSL_CERT_EXPIRY_DAYS.labels(domain=domain, port=str(port)).set(days)
        SSL_CERT_VALID.labels(domain=domain, port=str(port)).set(1)
        SSL_CERT_START_TS.labels(domain=domain, port=str(port)).set(start_d.timestamp())
        SSL_CERT_EXPIRY_TS.labels(domain=domain, port=str(port)).set(expiry.timestamp())
        log.info("%-40s port=%-5s days_left=%-4d", domain, port, days)

    except ssl.SSLCertVerificationError as e:
        log.warning("Invalid cert %s:%s — %s", domain, port, e)
        SSL_CERT_VALID.labels(domain=domain, port=str(port)).set(0)
        SSL_CERT_EXPIRY_DAYS.labels(domain=domain, port=str(port)).set(-1)
    except Exception as e:
        log.warning("Failed to probe %s:%s — %s", domain, port, e)
        SSL_CERT_VALID.labels(domain=domain, port=str(port)).set(0)
        SSL_CERT_EXPIRY_DAYS.labels(domain=domain, port=str(port)).set(-1)
    finally:
        SSL_PROBE_DURATION.labels(domain=domain, port=str(port)).set(time.time() - start)

def main():
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    listen_port = cfg.get("listen_port", 9115)
    interval    = cfg.get("scrape_interval", 3600)
    timeout     = cfg.get("timeout_seconds", 10)
    targets     = cfg.get("targets", [])
    workers     = cfg.get("workers", 10)

    start_http_server(listen_port)
    log.info("SSL cert exporter started on :%d — checking %d targets", listen_port, len(targets))

    while True:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = []
            for t in targets:
                if isinstance(t, str):
                    futures.append(ex.submit(check_cert, t, 443, timeout))
                elif isinstance(t, dict):
                    futures.append(ex.submit(check_cert, t["domain"], t.get("port", 443), timeout))
            concurrent.futures.wait(futures)
        log.info("Cert check cycle complete. Next in %ds", interval)
        time.sleep(interval)

if __name__ == "__main__":
    main()
