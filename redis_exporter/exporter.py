"""
Redis Prometheus Exporter
Exports: hit rate, eviction rate, memory fragmentation, connected clients,
blocked clients, keyspace stats, replication info, command latency.
"""

import time
import logging
import socket
import yaml
from prometheus_client import start_http_server, Gauge, Counter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Metrics ───────────────────────────────────────────────────────────────────
REDIS_UP               = Gauge("redis_up",                              "Redis is reachable")
REDIS_UPTIME           = Gauge("redis_uptime_seconds",                  "Redis server uptime")
REDIS_CLIENTS          = Gauge("redis_connected_clients",               "Connected clients")
REDIS_BLOCKED_CLIENTS  = Gauge("redis_blocked_clients",                 "Blocked clients")
REDIS_MEM_USED         = Gauge("redis_memory_used_bytes",               "Memory used by Redis")
REDIS_MEM_PEAK         = Gauge("redis_memory_used_peak_bytes",          "Peak memory used")
REDIS_MEM_FRAGMENTATION= Gauge("redis_memory_fragmentation_ratio",      "Memory fragmentation ratio")
REDIS_MEM_RSS          = Gauge("redis_memory_rss_bytes",                "RSS memory from OS")
REDIS_HITS             = Gauge("redis_keyspace_hits_total",             "Keyspace hits")
REDIS_MISSES           = Gauge("redis_keyspace_misses_total",           "Keyspace misses")
REDIS_HIT_RATE         = Gauge("redis_hit_rate_percent",                "Cache hit rate percentage")
REDIS_EVICTIONS        = Gauge("redis_evicted_keys_total",              "Total evicted keys")
REDIS_EXPIRED_KEYS     = Gauge("redis_expired_keys_total",              "Total expired keys")
REDIS_TOTAL_COMMANDS   = Gauge("redis_total_commands_processed",        "Commands processed")
REDIS_TOTAL_CONNECTIONS= Gauge("redis_total_connections_received",      "Total connections received")
REDIS_OPS_PER_SEC      = Gauge("redis_instantaneous_ops_per_sec",       "Instantaneous ops/sec")
REDIS_KEYSPACE_KEYS    = Gauge("redis_keyspace_keys",                   "Keys per database",     ["db"])
REDIS_KEYSPACE_EXPIRES = Gauge("redis_keyspace_expires",                "Keys with TTL per db",  ["db"])
REDIS_REPL_OFFSET      = Gauge("redis_replication_offset",              "Replication offset")
REDIS_REPL_LAG         = Gauge("redis_replication_lag_bytes",           "Slave replication lag", ["slave"])
REDIS_ROLE             = Gauge("redis_instance_role",                   "Role: 1=master, 0=slave")
REDIS_RDB_LAST_SAVE    = Gauge("redis_rdb_last_bgsave_duration_sec",    "Last RDB save duration")

def redis_command(sock, *args) -> str:
    cmd = f"*{len(args)}\r\n" + "".join(f"${len(str(a))}\r\n{a}\r\n" for a in args)
    sock.sendall(cmd.encode())
    return recv_response(sock)

def recv_response(sock) -> str:
    buf = b""
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
        if b"\r\n" in buf:
            break
    return buf.decode(errors="replace")

def parse_info(raw: str) -> dict:
    result = {}
    for line in raw.split("\r\n"):
        if line.startswith("#") or not line.strip():
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            result[k.strip()] = v.strip()
    return result

def collect(host: str, port: int, password: str):
    try:
        sock = socket.create_connection((host, port), timeout=5)
        if password:
            redis_command(sock, "AUTH", password)
        raw = redis_command(sock, "INFO", "all")
        sock.close()

        # strip leading type byte
        if raw.startswith("$"):
            raw = "\r\n".join(raw.split("\r\n")[1:])

        info = parse_info(raw)
        REDIS_UP.set(1)

        REDIS_UPTIME.set(float(info.get("uptime_in_seconds", 0)))
        REDIS_CLIENTS.set(float(info.get("connected_clients", 0)))
        REDIS_BLOCKED_CLIENTS.set(float(info.get("blocked_clients", 0)))
        REDIS_MEM_USED.set(float(info.get("used_memory", 0)))
        REDIS_MEM_PEAK.set(float(info.get("used_memory_peak", 0)))
        REDIS_MEM_RSS.set(float(info.get("used_memory_rss", 0)))
        REDIS_MEM_FRAGMENTATION.set(float(info.get("mem_fragmentation_ratio", 0)))
        REDIS_EVICTIONS.set(float(info.get("evicted_keys", 0)))
        REDIS_EXPIRED_KEYS.set(float(info.get("expired_keys", 0)))
        REDIS_TOTAL_COMMANDS.set(float(info.get("total_commands_processed", 0)))
        REDIS_TOTAL_CONNECTIONS.set(float(info.get("total_connections_received", 0)))
        REDIS_OPS_PER_SEC.set(float(info.get("instantaneous_ops_per_sec", 0)))
        REDIS_REPL_OFFSET.set(float(info.get("master_repl_offset", 0)))

        hits   = float(info.get("keyspace_hits", 0))
        misses = float(info.get("keyspace_misses", 0))
        REDIS_HITS.set(hits)
        REDIS_MISSES.set(misses)
        total = hits + misses
        REDIS_HIT_RATE.set(round(hits / total * 100, 2) if total > 0 else 0)

        # Role
        REDIS_ROLE.set(1 if info.get("role") == "master" else 0)

        # Keyspace
        for key, val in info.items():
            if key.startswith("db"):
                parts = {kv.split("=")[0]: kv.split("=")[1] for kv in val.split(",")}
                REDIS_KEYSPACE_KEYS.labels(db=key).set(float(parts.get("keys", 0)))
                REDIS_KEYSPACE_EXPIRES.labels(db=key).set(float(parts.get("expires", 0)))

        # Slave lag
        for key, val in info.items():
            if key.startswith("slave"):
                parts = {kv.split("=")[0]: kv.split("=")[1] for kv in val.split(",") if "=" in kv}
                REDIS_REPL_LAG.labels(slave=key).set(float(parts.get("lag", 0)))

    except Exception as e:
        REDIS_UP.set(0)
        log.error("Redis collect error: %s", e)

def main():
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    port     = cfg.get("listen_port", 9121)
    host     = cfg.get("redis", {}).get("host", "localhost")
    r_port   = cfg.get("redis", {}).get("port", 6379)
    password = cfg.get("redis", {}).get("password", "")
    interval = cfg.get("scrape_interval", 15)

    start_http_server(port)
    log.info("Redis exporter started on :%d", port)

    while True:
        collect(host, r_port, password)
        time.sleep(interval)

if __name__ == "__main__":
    main()
