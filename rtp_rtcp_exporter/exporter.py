"""
RTP/RTCP Prometheus Exporter
Monitors SIP voice quality metrics: packet loss, jitter, latency, MOS score.
Parses rtpproxy / Kamailio log output or reads from a UDP RTCP listener.
"""

import time
import re
import socket
import struct
import threading
import logging
from collections import defaultdict
import yaml
from prometheus_client import start_http_server, Gauge, Counter, Histogram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Metrics ──────────────────────────────────────────────────────────────────
RTP_PACKET_LOSS     = Gauge("rtp_packet_loss_percent",     "RTP packet loss percentage",          ["call_id", "direction"])
RTP_JITTER_MS       = Gauge("rtp_jitter_milliseconds",     "RTP jitter in milliseconds",          ["call_id", "direction"])
RTP_LATENCY_MS      = Gauge("rtp_latency_milliseconds",    "Round-trip latency in milliseconds",  ["call_id"])
RTP_MOS_SCORE       = Gauge("rtp_mos_score",               "Estimated MOS score (1.0-4.5)",       ["call_id"])
RTP_CALLS_ACTIVE    = Gauge("rtp_calls_active_total",      "Number of active RTP streams")
RTP_PACKETS_TOTAL   = Counter("rtp_packets_total",         "Total RTP packets processed",         ["call_id", "direction"])
RTP_BITRATE_KBPS    = Gauge("rtp_bitrate_kbps",            "RTP stream bitrate in kbps",          ["call_id", "direction"])
RTCP_RR_RECEIVED    = Counter("rtcp_receiver_reports_total","Total RTCP receiver reports received")
RTCP_SR_RECEIVED    = Counter("rtcp_sender_reports_total",  "Total RTCP sender reports received")

# ── MOS Calculation (E-Model simplified) ─────────────────────────────────────
def calculate_mos(packet_loss_pct: float, jitter_ms: float, latency_ms: float) -> float:
    """Estimate MOS score using simplified E-Model (ITU-T G.107)."""
    r = 93.2
    r -= packet_loss_pct * 2.5
    r -= (jitter_ms / 10.0) * 0.5
    if latency_ms > 150:
        r -= (latency_ms - 150) * 0.1
    r = max(0.0, min(100.0, r))
    if r < 0:
        return 1.0
    mos = 1 + 0.035 * r + r * (r - 60) * (100 - r) * 7e-6
    return round(max(1.0, min(4.5, mos)), 2)

# ── RTCP Packet Parser ────────────────────────────────────────────────────────
def parse_rtcp_packet(data: bytes) -> dict:
    """Parse RTCP SR/RR packet and extract quality metrics."""
    result = {}
    if len(data) < 4:
        return result
    try:
        header = struct.unpack("!BBH", data[:4])
        pt = header[1]  # packet type: 200=SR, 201=RR

        if pt == 200:  # Sender Report
            RTCP_SR_RECEIVED.inc()
            if len(data) >= 28:
                ssrc, ntp_msw, ntp_lsw, rtp_ts, pkt_count, oct_count = struct.unpack("!IIIIII", data[4:28])
                result["type"] = "SR"
                result["ssrc"] = ssrc
                result["packet_count"] = pkt_count

        if pt in (200, 201) and len(data) >= 32:  # RR block
            RTCP_RR_RECEIVED.inc()
            offset = 28 if pt == 200 else 8
            if len(data) >= offset + 24:
                rr = struct.unpack("!IBBHIIII", data[offset:offset + 24])
                fraction_lost = rr[1] / 256.0 * 100
                jitter = rr[4] / 90.0  # samples → ms (assuming 90kHz clock)
                result.update({
                    "packet_loss_pct": round(fraction_lost, 2),
                    "jitter_ms": round(jitter, 2),
                    "ssrc": rr[0],
                })
    except struct.error as e:
        log.debug("RTCP parse error: %s", e)
    return result

# ── Log File Parser ───────────────────────────────────────────────────────────
LOG_PATTERN = re.compile(
    r"call_id=(?P<call_id>\S+).*"
    r"loss=(?P<loss>[\d.]+).*"
    r"jitter=(?P<jitter>[\d.]+).*"
    r"latency=(?P<latency>[\d.]+)"
)

def parse_log_line(line: str):
    m = LOG_PATTERN.search(line)
    if not m:
        return
    call_id = m.group("call_id")
    loss    = float(m.group("loss"))
    jitter  = float(m.group("jitter"))
    latency = float(m.group("latency"))
    mos     = calculate_mos(loss, jitter, latency)

    RTP_PACKET_LOSS.labels(call_id=call_id, direction="rx").set(loss)
    RTP_JITTER_MS.labels(call_id=call_id, direction="rx").set(jitter)
    RTP_LATENCY_MS.labels(call_id=call_id).set(latency)
    RTP_MOS_SCORE.labels(call_id=call_id).set(mos)

def tail_log(path: str):
    log.info("Tailing log file: %s", path)
    try:
        with open(path) as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    parse_log_line(line)
                else:
                    time.sleep(0.1)
    except FileNotFoundError:
        log.warning("Log file not found: %s — using simulated data", path)
        simulate_metrics()

# ── UDP RTCP Listener ─────────────────────────────────────────────────────────
def listen_rtcp(host: str, port: int):
    log.info("Listening for RTCP on %s:%d", host, port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, port))
    active_calls = set()
    while True:
        try:
            data, addr = sock.recvfrom(4096)
            metrics = parse_rtcp_packet(data)
            if metrics:
                call_id = str(metrics.get("ssrc", addr[0]))
                active_calls.add(call_id)
                RTP_CALLS_ACTIVE.set(len(active_calls))
                if "packet_loss_pct" in metrics:
                    loss    = metrics["packet_loss_pct"]
                    jitter  = metrics.get("jitter_ms", 0)
                    latency = 0
                    RTP_PACKET_LOSS.labels(call_id=call_id, direction="rx").set(loss)
                    RTP_JITTER_MS.labels(call_id=call_id, direction="rx").set(jitter)
                    RTP_MOS_SCORE.labels(call_id=call_id).set(calculate_mos(loss, jitter, latency))
        except Exception as e:
            log.error("RTCP listener error: %s", e)

# ── Simulation (demo mode when no live data) ──────────────────────────────────
def simulate_metrics():
    import random
    calls = ["call-1001", "call-1002", "call-1003"]
    while True:
        RTP_CALLS_ACTIVE.set(len(calls))
        for cid in calls:
            loss    = random.uniform(0, 3)
            jitter  = random.uniform(1, 20)
            latency = random.uniform(10, 120)
            mos     = calculate_mos(loss, jitter, latency)
            RTP_PACKET_LOSS.labels(call_id=cid, direction="rx").set(loss)
            RTP_PACKET_LOSS.labels(call_id=cid, direction="tx").set(random.uniform(0, 2))
            RTP_JITTER_MS.labels(call_id=cid, direction="rx").set(jitter)
            RTP_LATENCY_MS.labels(call_id=cid).set(latency)
            RTP_MOS_SCORE.labels(call_id=cid).set(mos)
            RTP_BITRATE_KBPS.labels(call_id=cid, direction="rx").set(random.uniform(60, 128))
        time.sleep(10)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    port      = cfg.get("listen_port", 9300)
    log_file  = cfg.get("log_file", "")
    rtcp_host = cfg.get("rtcp_listen_host", "0.0.0.0")
    rtcp_port = cfg.get("rtcp_listen_port", 0)

    start_http_server(port)
    log.info("RTP/RTCP exporter started on :%d", port)

    if rtcp_port:
        t = threading.Thread(target=listen_rtcp, args=(rtcp_host, rtcp_port), daemon=True)
        t.start()

    if log_file:
        tail_log(log_file)
    else:
        simulate_metrics()

if __name__ == "__main__":
    main()
