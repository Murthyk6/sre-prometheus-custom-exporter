# FortiGate Prometheus Exporter User Guide

This guide explains how to install, configure, run, and integrate the FortiGate Prometheus Exporter for monitoring FortiGate firewalls.

---

## 1. Prerequisites

- Python 3.8+ (or use Docker)
- Access to FortiGate firewalls with API tokens
- Prometheus server (for integration)

---

## 2. Installation

### Option A: Native Python

1. Clone the repository:
    ```sh
    git clone <your-repo-url>
    cd sre-prometheus-custom-exporter/fortigate_exporter
    ```

2. Install dependencies:
    ```sh
    pip install -r requirements.txt
    ```

### Option B: Docker

1. Build the Docker image:
    ```sh
    docker build -t fortigate-exporter .
    ```

---

## 3. Configuration

Edit `config.yaml` to define your FortiGate firewalls:

```yaml
firewalls:
  - name: fortigate-1
    ip: "fw1.example.com"
    port: "443"
    token: "your-api-token-1"
  - name: fortigate-2
    ip: "fw2.example.com"
    port: "443"
    token: "your-api-token-2"
```

- `name`: Unique identifier for the firewall
- `ip`: Hostname or IP address of the firewall
- `port`: API port (usually 443)
- `token`: API access token

---

## 4. Running the Exporter

### Option A: Native Python

```sh
python exporter.py
```

- By default, the exporter listens on port `9200`.
- To change the port:
    ```sh
    EXPORTER_PORT=9300 python exporter.py
    ```
- To use a custom config file:
    ```sh
    CONFIG_FILE=custom_config.yaml python exporter.py
    ```

### Option B: Docker

```sh
docker run -d -p 9200:9200 \
  -v $(pwd)/config.yaml:/app/config.yaml \
  --name fortigate-exporter fortigate-exporter
```

---

## 5. Prometheus Integration

Add the following scrape config to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'fortigate'
    static_configs:
      - targets: ['<exporter_host>:9200']
```

Replace `<exporter_host>` with the hostname or IP where the exporter is running.

---

## 6. Metrics Exposed

The exporter provides metrics such as:

- Interface statistics (bytes, packets, link status)
- CPU, memory, and session usage
- Firewall uptime
- IPSec VPN tunnel status and traffic

Access metrics at: [http://localhost:9200/metrics](http://localhost:9200/metrics)

---

## 7. Troubleshooting

- Ensure API tokens are valid and have necessary permissions.
- Check firewall connectivity from the exporter host.
- Review exporter logs for errors.

---

## 8. Support

For issues or feature requests, open an issue in the repository.

