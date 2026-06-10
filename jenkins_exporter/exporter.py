"""
Jenkins Prometheus Exporter
Exports: build queue depth, per-job build status/duration,
executor utilization, last build result, and build trend.
"""

import time
import logging
import urllib.request
import urllib.parse
import base64
import json
import yaml
from prometheus_client import start_http_server, Gauge, Counter, Histogram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Metrics ───────────────────────────────────────────────────────────────────
JENKINS_UP              = Gauge("jenkins_up",                      "Jenkins is reachable")
JENKINS_QUEUE_SIZE      = Gauge("jenkins_queue_size",              "Build queue depth")
JENKINS_EXECUTORS_TOTAL = Gauge("jenkins_executors_total",         "Total executors",        ["node"])
JENKINS_EXECUTORS_BUSY  = Gauge("jenkins_executors_busy",          "Busy executors",         ["node"])
JENKINS_EXECUTOR_PCT    = Gauge("jenkins_executor_utilization_pct","Executor utilization %", ["node"])
JENKINS_BUILD_RESULT    = Gauge("jenkins_job_last_build_result",   "Last build: 1=SUCCESS,0=FAIL,-1=UNKNOWN", ["job"])
JENKINS_BUILD_DURATION  = Gauge("jenkins_job_last_build_duration_seconds", "Last build duration", ["job"])
JENKINS_BUILD_NUMBER    = Gauge("jenkins_job_last_build_number",   "Last build number",      ["job"])
JENKINS_BUILD_AGE       = Gauge("jenkins_job_last_build_age_seconds","Seconds since last build", ["job"])
JENKINS_BUILD_TOTAL     = Counter("jenkins_builds_total",          "Total builds by result", ["job", "result"])
JENKINS_JOB_HEALTH      = Gauge("jenkins_job_health_score",        "Job health score 0-100", ["job"])

RESULT_MAP = {"SUCCESS": 1, "UNSTABLE": 0.5, "FAILURE": 0, "ABORTED": -0.5, None: -1}

def jenkins_get(url: str, user: str, token: str) -> dict:
    req = urllib.request.Request(url)
    if user and token:
        creds = base64.b64encode(f"{user}:{token}".encode()).decode()
        req.add_header("Authorization", f"Basic {creds}")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def collect(base_url: str, user: str, token: str):
    api = base_url.rstrip("/") + "/api/json"
    try:
        # Overall Jenkins info
        info = jenkins_get(f"{api}?tree=jobs[name,url,healthReport[score],lastBuild[number,result,duration,timestamp]],overallLoad[busyExecutors[value],totalExecutors[value]]", user, token)
        JENKINS_UP.set(1)

        # Queue depth
        try:
            q = jenkins_get(f"{base_url.rstrip('/')}/queue/api/json?tree=items[id]", user, token)
            JENKINS_QUEUE_SIZE.set(len(q.get("items", [])))
        except Exception:
            pass

        # Executors
        try:
            computers = jenkins_get(f"{base_url.rstrip('/')}/computer/api/json?tree=computer[displayName,executors[idle],oneOffExecutors[idle]]", user, token)
            for node in computers.get("computer", []):
                name  = node.get("displayName", "master")
                execs = node.get("executors", []) + node.get("oneOffExecutors", [])
                total = len(execs)
                busy  = sum(1 for e in execs if not e.get("idle", True))
                JENKINS_EXECUTORS_TOTAL.labels(node=name).set(total)
                JENKINS_EXECUTORS_BUSY.labels(node=name).set(busy)
                JENKINS_EXECUTOR_PCT.labels(node=name).set(round(busy / total * 100, 1) if total else 0)
        except Exception:
            pass

        # Per-job metrics
        now_ms = time.time() * 1000
        for job in info.get("jobs", []):
            job_name = job.get("name", "unknown")
            health   = job.get("healthReport", [{}])[0].get("score", 0) if job.get("healthReport") else 0
            JENKINS_JOB_HEALTH.labels(job=job_name).set(health)

            lb = job.get("lastBuild")
            if lb:
                result   = lb.get("result")
                duration = lb.get("duration", 0) / 1000.0
                number   = lb.get("number", 0)
                ts       = lb.get("timestamp", now_ms)
                age      = (now_ms - ts) / 1000.0

                JENKINS_BUILD_RESULT.labels(job=job_name).set(RESULT_MAP.get(result, -1))
                JENKINS_BUILD_DURATION.labels(job=job_name).set(duration)
                JENKINS_BUILD_NUMBER.labels(job=job_name).set(number)
                JENKINS_BUILD_AGE.labels(job=job_name).set(age)

    except Exception as e:
        JENKINS_UP.set(0)
        log.error("Jenkins collect error: %s", e)

def main():
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    port     = cfg.get("listen_port", 9500)
    interval = cfg.get("scrape_interval", 30)
    base_url = cfg.get("jenkins", {}).get("url", "http://localhost:8080")
    user     = cfg.get("jenkins", {}).get("user", "")
    token    = cfg.get("jenkins", {}).get("api_token", "")

    start_http_server(port)
    log.info("Jenkins exporter started on :%d", port)

    while True:
        collect(base_url, user, token)
        time.sleep(interval)

if __name__ == "__main__":
    main()
