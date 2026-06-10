"""
RabbitMQ Prometheus Exporter
Uses RabbitMQ Management HTTP API to export queue depth, consumer counts,
message rates, memory/disk alarms, and node health.
"""

import time
import logging
import urllib.request
import base64
import json
import yaml
from prometheus_client import start_http_server, Gauge, Counter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

RABBITMQ_UP              = Gauge("rabbitmq_up",                         "RabbitMQ management API reachable")
QUEUE_MESSAGES           = Gauge("rabbitmq_queue_messages",             "Total messages in queue",        ["vhost", "queue"])
QUEUE_MESSAGES_READY     = Gauge("rabbitmq_queue_messages_ready",       "Ready messages in queue",        ["vhost", "queue"])
QUEUE_MESSAGES_UNACKED   = Gauge("rabbitmq_queue_messages_unacked",     "Unacknowledged messages",        ["vhost", "queue"])
QUEUE_CONSUMERS          = Gauge("rabbitmq_queue_consumers",            "Number of consumers",            ["vhost", "queue"])
QUEUE_PUBLISH_RATE       = Gauge("rabbitmq_queue_publish_rate",         "Publish message rate/s",         ["vhost", "queue"])
QUEUE_DELIVER_RATE       = Gauge("rabbitmq_queue_deliver_rate",         "Deliver message rate/s",         ["vhost", "queue"])
QUEUE_ACK_RATE           = Gauge("rabbitmq_queue_ack_rate",             "Ack rate/s",                     ["vhost", "queue"])
NODE_MEM_USED            = Gauge("rabbitmq_node_mem_used_bytes",        "Node memory used",               ["node"])
NODE_MEM_LIMIT           = Gauge("rabbitmq_node_mem_limit_bytes",       "Node memory limit",              ["node"])
NODE_MEM_ALARM           = Gauge("rabbitmq_node_mem_alarm",             "1 if memory alarm triggered",    ["node"])
NODE_DISK_ALARM          = Gauge("rabbitmq_node_disk_free_alarm",       "1 if disk alarm triggered",      ["node"])
NODE_FD_USED             = Gauge("rabbitmq_node_fd_used",               "File descriptors in use",        ["node"])
NODE_SOCKETS_USED        = Gauge("rabbitmq_node_sockets_used",          "Sockets in use",                 ["node"])
NODE_PROC_USED           = Gauge("rabbitmq_node_proc_used",             "Erlang processes used",          ["node"])
NODE_RUNNING             = Gauge("rabbitmq_node_running",               "1 if node is running",           ["node"])
OVERVIEW_TOTAL_QUEUES    = Gauge("rabbitmq_queues_total",               "Total queues in cluster")
OVERVIEW_TOTAL_CONSUMERS = Gauge("rabbitmq_consumers_total",            "Total consumers in cluster")
OVERVIEW_MESSAGES        = Gauge("rabbitmq_messages_total",             "Total messages in cluster")

def rmq_get(base_url: str, path: str, user: str, password: str):
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    req = urllib.request.Request(url)
    creds = base64.b64encode(f"{user}:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {creds}")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def collect(base_url: str, user: str, password: str):
    try:
        overview = rmq_get(base_url, "/api/overview", user, password)
        RABBITMQ_UP.set(1)

        OVERVIEW_TOTAL_QUEUES.set(overview.get("object_totals", {}).get("queues", 0))
        OVERVIEW_TOTAL_CONSUMERS.set(overview.get("object_totals", {}).get("consumers", 0))
        OVERVIEW_MESSAGES.set(overview.get("queue_totals", {}).get("messages", 0))

    except Exception as e:
        RABBITMQ_UP.set(0)
        log.error("RabbitMQ overview error: %s", e)
        return

    # Queues
    try:
        queues = rmq_get(base_url, "/api/queues", user, password)
        for q in queues:
            vhost = q.get("vhost", "/")
            name  = q.get("name", "unknown")
            QUEUE_MESSAGES.labels(vhost=vhost, queue=name).set(q.get("messages", 0))
            QUEUE_MESSAGES_READY.labels(vhost=vhost, queue=name).set(q.get("messages_ready", 0))
            QUEUE_MESSAGES_UNACKED.labels(vhost=vhost, queue=name).set(q.get("messages_unacknowledged", 0))
            QUEUE_CONSUMERS.labels(vhost=vhost, queue=name).set(q.get("consumers", 0))

            rates = q.get("message_stats", {})
            QUEUE_PUBLISH_RATE.labels(vhost=vhost, queue=name).set(rates.get("publish_details", {}).get("rate", 0))
            QUEUE_DELIVER_RATE.labels(vhost=vhost, queue=name).set(rates.get("deliver_details", {}).get("rate", 0))
            QUEUE_ACK_RATE.labels(vhost=vhost, queue=name).set(rates.get("ack_details", {}).get("rate", 0))
    except Exception as e:
        log.error("Queue metrics error: %s", e)

    # Nodes
    try:
        nodes = rmq_get(base_url, "/api/nodes", user, password)
        for node in nodes:
            name = node.get("name", "unknown")
            NODE_RUNNING.labels(node=name).set(1 if node.get("running") else 0)
            NODE_MEM_USED.labels(node=name).set(node.get("mem_used", 0))
            NODE_MEM_LIMIT.labels(node=name).set(node.get("mem_limit", 0))
            NODE_MEM_ALARM.labels(node=name).set(1 if node.get("mem_alarm") else 0)
            NODE_DISK_ALARM.labels(node=name).set(1 if node.get("disk_free_alarm") else 0)
            NODE_FD_USED.labels(node=name).set(node.get("fd_used", 0))
            NODE_SOCKETS_USED.labels(node=name).set(node.get("sockets_used", 0))
            NODE_PROC_USED.labels(node=name).set(node.get("proc_used", 0))
    except Exception as e:
        log.error("Node metrics error: %s", e)

def main():
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    port     = cfg.get("listen_port", 9419)
    interval = cfg.get("scrape_interval", 15)
    rmq      = cfg.get("rabbitmq", {})

    start_http_server(port)
    log.info("RabbitMQ exporter started on :%d", port)

    while True:
        collect(rmq.get("url", "http://localhost:15672"), rmq.get("user", "guest"), rmq.get("password", "guest"))
        time.sleep(interval)

if __name__ == "__main__":
    main()
