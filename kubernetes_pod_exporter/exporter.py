"""
Kubernetes Pod Prometheus Exporter
Exports pod restart counts, OOMKill events, pending pod age,
resource requests vs limits, and crash-looping pods.
Uses the in-cluster service account or kubeconfig.
"""

import time
import logging
import os
import yaml
from prometheus_client import start_http_server, Gauge, Counter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

try:
    from kubernetes import client, config as k8s_config
    K8S_AVAILABLE = True
except ImportError:
    K8S_AVAILABLE = False
    log.warning("kubernetes package not installed — running in simulation mode")

# ── Metrics ───────────────────────────────────────────────────────────────────
POD_RESTARTS        = Gauge("k8s_pod_restart_count",          "Pod container restart count",
                            ["namespace", "pod", "container"])
POD_PHASE           = Gauge("k8s_pod_phase",                  "Pod phase (1=Running,2=Pending,3=Failed,4=Succeeded)",
                            ["namespace", "pod", "phase"])
POD_PENDING_SECONDS = Gauge("k8s_pod_pending_duration_seconds","Seconds a pod has been in Pending state",
                            ["namespace", "pod"])
POD_OOM_KILLED      = Gauge("k8s_pod_oomkill_total",          "Number of OOMKilled container restarts",
                            ["namespace", "pod", "container"])
POD_CRASHLOOP       = Gauge("k8s_pod_crashloopbackoff",       "1 if pod is in CrashLoopBackOff",
                            ["namespace", "pod", "container"])
POD_CPU_REQUEST     = Gauge("k8s_pod_cpu_request_millicores",  "CPU requested in millicores",
                            ["namespace", "pod", "container"])
POD_MEM_REQUEST     = Gauge("k8s_pod_memory_request_bytes",    "Memory requested in bytes",
                            ["namespace", "pod", "container"])
POD_CPU_LIMIT       = Gauge("k8s_pod_cpu_limit_millicores",    "CPU limit in millicores",
                            ["namespace", "pod", "container"])
POD_MEM_LIMIT       = Gauge("k8s_pod_memory_limit_bytes",      "Memory limit in bytes",
                            ["namespace", "pod", "container"])
PODS_TOTAL          = Gauge("k8s_pods_total",                  "Total pods by namespace and phase",
                            ["namespace", "phase"])

PHASE_MAP = {"Running": 1, "Pending": 2, "Failed": 3, "Succeeded": 4, "Unknown": 0}

def parse_cpu(cpu_str: str) -> float:
    if not cpu_str:
        return 0.0
    if cpu_str.endswith("m"):
        return float(cpu_str[:-1])
    return float(cpu_str) * 1000

def parse_mem(mem_str: str) -> float:
    if not mem_str:
        return 0.0
    units = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4, "K": 1000, "M": 1e6, "G": 1e9}
    for suffix, mult in units.items():
        if mem_str.endswith(suffix):
            return float(mem_str[:-len(suffix)]) * mult
    return float(mem_str)

def collect_metrics(v1, namespaces: list):
    ns_list = namespaces if namespaces else [None]
    phase_counts: dict = {}

    for ns in ns_list:
        try:
            pods = v1.list_namespaced_pod(ns).items if ns else v1.list_pod_for_all_namespaces().items
        except Exception as e:
            log.error("Failed to list pods: %s", e)
            continue

        for pod in pods:
            namespace = pod.metadata.namespace
            pod_name  = pod.metadata.name
            phase     = pod.status.phase or "Unknown"

            # Phase gauge
            POD_PHASE.labels(namespace=namespace, pod=pod_name, phase=phase).set(PHASE_MAP.get(phase, 0))
            phase_counts[(namespace, phase)] = phase_counts.get((namespace, phase), 0) + 1

            # Pending duration
            if phase == "Pending" and pod.metadata.creation_timestamp:
                import datetime
                age = (datetime.datetime.now(datetime.timezone.utc) - pod.metadata.creation_timestamp).total_seconds()
                POD_PENDING_SECONDS.labels(namespace=namespace, pod=pod_name).set(age)

            # Container statuses
            for cs in (pod.status.container_statuses or []):
                cname = cs.name
                POD_RESTARTS.labels(namespace=namespace, pod=pod_name, container=cname).set(cs.restart_count)

                # OOMKill detection
                if cs.last_state and cs.last_state.terminated and cs.last_state.terminated.reason == "OOMKilled":
                    POD_OOM_KILLED.labels(namespace=namespace, pod=pod_name, container=cname).set(cs.restart_count)

                # CrashLoopBackOff
                crash = 0
                if cs.state and cs.state.waiting and cs.state.waiting.reason == "CrashLoopBackOff":
                    crash = 1
                POD_CRASHLOOP.labels(namespace=namespace, pod=pod_name, container=cname).set(crash)

            # Resource requests/limits
            for c in (pod.spec.containers or []):
                cname = c.name
                req = c.resources.requests or {} if c.resources else {}
                lim = c.resources.limits  or {} if c.resources else {}
                POD_CPU_REQUEST.labels(namespace=namespace, pod=pod_name, container=cname).set(parse_cpu(req.get("cpu", "0")))
                POD_MEM_REQUEST.labels(namespace=namespace, pod=pod_name, container=cname).set(parse_mem(req.get("memory", "0")))
                POD_CPU_LIMIT.labels(namespace=namespace, pod=pod_name, container=cname).set(parse_cpu(lim.get("cpu", "0")))
                POD_MEM_LIMIT.labels(namespace=namespace, pod=pod_name, container=cname).set(parse_mem(lim.get("memory", "0")))

    for (ns, ph), count in phase_counts.items():
        PODS_TOTAL.labels(namespace=ns, phase=ph).set(count)

def main():
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    port       = cfg.get("listen_port", 9400)
    namespaces = cfg.get("namespaces", [])
    interval   = cfg.get("scrape_interval", 30)
    in_cluster = cfg.get("in_cluster", False)

    start_http_server(port)
    log.info("Kubernetes pod exporter started on :%d", port)

    if K8S_AVAILABLE:
        if in_cluster:
            k8s_config.load_incluster_config()
        else:
            k8s_config.load_kube_config()
        v1 = client.CoreV1Api()
        while True:
            collect_metrics(v1, namespaces)
            time.sleep(interval)
    else:
        log.warning("Kubernetes client unavailable — install: pip install kubernetes")
        while True:
            time.sleep(interval)

if __name__ == "__main__":
    main()
