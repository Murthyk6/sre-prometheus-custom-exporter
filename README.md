# 📡 SRE Prometheus Custom Exporter — Fortigate

> A production-ready custom Prometheus exporter for FortiGate firewall/network devices, enabling SRE and DevOps teams to bring network security appliance metrics into their Prometheus/Grafana observability stack. Containerized with Docker for easy deployment alongside existing monitoring infrastructure.

![Python](https://img.shields.io/badge/Python-3.x-3776AB?style=flat-square&logo=python&logoColor=white)
![Prometheus](https://img.shields.io/badge/Prometheus-Exporter-E6522C?style=flat-square&logo=prometheus&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Containerized-2496ED?style=flat-square&logo=docker&logoColor=white)
![Grafana](https://img.shields.io/badge/Grafana-Dashboard_Ready-F46800?style=flat-square&logo=grafana&logoColor=white)
![FortiGate](https://img.shields.io/badge/FortiGate-Network-EE3124?style=flat-square)

---

## Overview

Standard Prometheus exporters don't cover network security appliances. This exporter bridges that gap — scraping FortiGate device metrics via API and exposing them as Prometheus metrics, enabling unified dashboards for infrastructure + network visibility.

**Part of a broader custom exporter toolkit** also including RTP/RTCP voice traffic exporters (packet loss, jitter, latency) built for real-time SIP monitoring at Ubona Technologies.

---

## What It Monitors

| Metric Category | Description |
|---|---|
| Interface stats | Bytes in/out, packet counts per interface |
| Session table | Active sessions, session rate |
| CPU / Memory | FortiGate system resource utilization |
| VPN tunnels | Tunnel status and traffic |
| Firewall policies | Hit counts per policy |

---

## Quick Start

### Docker (recommended)

```bash
git clone https://github.com/Murthyk6/sre-prometheus-custom-exporter.git
cd sre-prometheus-custom-exporter/fortigate_exporter

# Configure your FortiGate credentials
cp config.yaml config.local.yaml
# Edit config.local.yaml with your device details

# Build and run
docker build -t fortigate-exporter .
docker run -d \
  -p 9200:9200 \
  -v $(pwd)/config.local.yaml:/app/config.yaml \
  fortigate-exporter
```

Metrics at: `http://localhost:9200/metrics`

### Run directly

```bash
pip install -r requirements.txt
python exporter.py --config config.yaml
```

---

## Configuration

```yaml
# config.yaml
fortigate:
  host: "192.168.1.1"
  port: 443
  username: "admin"
  password: "your_password"
  verify_ssl: false

exporter:
  listen_port: 9200
  scrape_interval: 30
```

---

## Prometheus Integration

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'fortigate'
    static_configs:
      - targets: ['<exporter-host>:9200']
    scrape_interval: 30s
```

---

## Grafana Queries

```promql
# Interface throughput
rate(fortigate_interface_bytes_total{direction="rx"}[5m])

# Active sessions
fortigate_session_count

# CPU utilization
fortigate_cpu_usage_percent
```

---

## Architecture

```
┌─────────────────┐     REST/HTTPS     ┌──────────────┐
│  FortiGate      │◄──────────────────►│  Exporter    │
│  Device         │                    │  (Python)    │
└─────────────────┘                    │  :9200       │
                                       └──────┬───────┘
                                              │ /metrics
                                       ┌──────▼───────┐
                                       │  Prometheus  │
                                       └──────┬───────┘
                                              │
                                       ┌──────▼───────┐
                                       │   Grafana    │
                                       └──────────────┘
```

---

## Related Exporters

This repo is part of a custom observability toolkit. See also:

- **[ProcessScout](https://github.com/Murthyk6/ProcessScout)** — Per-process CPU/memory exporter in Go (Java, Python, Node, Docker)
- **RTP/RTCP Voice Exporter** — Custom exporter for SIP traffic monitoring (packet loss, jitter, latency) — built at Ubona Technologies

---

> Built from real SRE work: extending Prometheus observability beyond standard exporters to cover network appliances and custom application metrics. Reflects the same approach used to build telecom observability systems at Ubona Technologies.
