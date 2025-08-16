from flask import Flask, Response
import requests
import urllib3
import yaml
import os

# Disable SSL warnings for self-signed FortiGate certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# Load firewall config from external YAML
CONFIG_FILE = os.getenv("CONFIG_FILE", "config.yaml")
with open(CONFIG_FILE, "r") as f:
    FIREWALLS = yaml.safe_load(f).get("firewalls", [])


def fetch_interface_stats(fw):
    """Fetch interface statistics for a firewall"""
    url = f"https://{fw['ip']}:{fw['port']}/api/v2/monitor/system/interface"
    headers = {"Authorization": f"Bearer {fw['token']}"}
    metrics = []

    try:
        response = requests.get(url, headers=headers, verify=False, timeout=(3.05, 10))
        response.raise_for_status()
        data = response.json().get("results", {})

        if isinstance(data, dict):
            for iface, stats in data.items():
                metrics.append(f'fortigate_interface_tx_bytes{{firewall="{fw["name"]}",interface="{iface}"}} {stats.get("tx_bytes", 0)}')
                metrics.append(f'fortigate_interface_rx_bytes{{firewall="{fw["name"]}",interface="{iface}"}} {stats.get("rx_bytes", 0)}')
                metrics.append(f'fortigate_interface_tx_packets{{firewall="{fw["name"]}",interface="{iface}"}} {stats.get("tx_packets", 0)}')
                metrics.append(f'fortigate_interface_rx_packets{{firewall="{fw["name"]}",interface="{iface}"}} {stats.get("rx_packets", 0)}')
                metrics.append(f'fortigate_interface_link_up{{firewall="{fw["name"]}",interface="{iface}"}} {1 if stats.get("link") else 0}')
        elif isinstance(data, list):
            for iface in data:
                name = iface.get("name", "unknown")
                metrics.append(f'fortigate_interface_tx_bytes{{firewall="{fw["name"]}",interface="{name}"}} {iface.get("tx_bytes",0)}')
                metrics.append(f'fortigate_interface_rx_bytes{{firewall="{fw["name"]}",interface="{name}"}} {iface.get("rx_bytes",0)}')
                metrics.append(f'fortigate_interface_tx_packets{{firewall="{fw["name"]}",interface="{name}"}} {iface.get("tx_packets", 0)}')
                metrics.append(f'fortigate_interface_rx_packets{{firewall="{fw["name"]}",interface="{name}"}} {iface.get("rx_packets", 0)}')
                metrics.append(f'fortigate_interface_link_up{{firewall="{fw["name"]}",interface="{name}"}} {1 if iface.get("link") else 0}')
        else:
            metrics.append(f'# Error: Unexpected format in interface data for {fw["name"]}')
    except Exception as e:
        metrics.append(f'# Error fetching interface stats for {fw["name"]}: {e}')

    return metrics


def fetch_resource_metrics(fw):
    """Fetch CPU, memory, and session usage"""
    url = f"https://{fw['ip']}:{fw['port']}/api/v2/monitor/system/resource/usage"
    headers = {"Authorization": f"Bearer {fw['token']}"}
    metrics = []

    try:
        response = requests.get(url, headers=headers, verify=False, timeout=(3.05, 10))
        response.raise_for_status()
        data = response.json().get("results", {})

        # CPU
        cpu = data.get("cpu", [{}])[0]
        metrics.append(f'fortigate_cpu_current{{firewall="{fw["name"]}"}} {cpu.get("current", 0)}')
        for interval in ["1-min", "10-min", "30-min"]:
            hist = cpu.get("historical", {}).get(interval, {})
            metrics.append(f'fortigate_cpu_avg{{firewall="{fw["name"]}",interval="{interval}"}} {hist.get("average", 0)}')
            metrics.append(f'fortigate_cpu_max{{firewall="{fw["name"]}",interval="{interval}"}} {hist.get("max", 0)}')
            metrics.append(f'fortigate_cpu_min{{firewall="{fw["name"]}",interval="{interval}"}} {hist.get("min", 0)}')

        # Memory
        mem = data.get("mem", [{}])[0]
        metrics.append(f'fortigate_memory_current{{firewall="{fw["name"]}"}} {mem.get("current", 0)}')
        for interval in ["1-min", "10-min", "30-min"]:
            hist = mem.get("historical", {}).get(interval, {})
            metrics.append(f'fortigate_memory_avg{{firewall="{fw["name"]}",interval="{interval}"}} {hist.get("average", 0)}')

        # Sessions
        session = data.get("session", [{}])[0]
        metrics.append(f'fortigate_session_current{{firewall="{fw["name"]}"}} {session.get("current", 0)}')
        for interval in ["1-min", "10-min", "30-min"]:
            hist = session.get("historical", {}).get(interval, {})
            metrics.append(f'fortigate_session_avg{{firewall="{fw["name"]}",interval="{interval}"}} {hist.get("average", 0)}')
            metrics.append(f'fortigate_session_max{{firewall="{fw["name"]}",interval="{interval}"}} {hist.get("max", 0)}')

    except Exception as e:
        metrics.append(f'# Error fetching resource stats for {fw["name"]}: {e}')

    return metrics


def fetch_uptime(fw):
    """Fetch firewall uptime"""
    url = f"https://{fw['ip']}:{fw['port']}/api/v2/monitor/system/ha-statistics"
    headers = {"Authorization": f"Bearer {fw['token']}"}
    metrics = []

    try:
        response = requests.get(url, headers=headers, verify=False, timeout=(3.05, 10))
        response.raise_for_status()
        results = response.json().get("results", [])
        if isinstance(results, list) and len(results) > 0:
            uptime = results[0].get("uptime", 0)
            metrics.append(f'fortigate_uptime_seconds{{firewall="{fw["name"]}"}} {uptime}')
        else:
            metrics.append(f'# Error: Unexpected format in uptime data for {fw["name"]}')
    except Exception as e:
        metrics.append(f'# Error fetching uptime for {fw["name"]}: {e}')

    return metrics


def fetch_ipsec_status(fw):
    """Fetch IPSec VPN tunnel status and traffic"""
    url = f"https://{fw['ip']}:{fw['port']}/api/v2/monitor/vpn/ipsec"
    headers = {"Authorization": f"Bearer {fw['token']}"}
    metrics = []

    try:
        response = requests.get(url, headers=headers, verify=False, timeout=(3.05, 10))
        response.raise_for_status()
        data = response.json()

        if data.get("status") == "success":
            for tunnel in data.get("results", []):
                name = tunnel.get("name", "unknown")
                remote_ip = tunnel.get("rgwy", "0.0.0.0")
                proxy_ids = tunnel.get("proxyid", [])
                status = proxy_ids[0].get("status", "unknown") if proxy_ids else "unknown"
                value = 1 if status.lower() == "up" else 0

                # Status Metric
                metrics.append(f'fortigate_ipsec_status{{firewall="{fw["name"]}",tunnel="{name}",peer="{remote_ip}"}} {value}')

                # Traffic
                metrics.append(f'fortigate_ipsec_in_bytes{{firewall="{fw["name"]}",tunnel="{name}",peer="{remote_ip}"}} {tunnel.get("incoming_bytes", 0)}')
                metrics.append(f'fortigate_ipsec_out_bytes{{firewall="{fw["name"]}",tunnel="{name}",peer="{remote_ip}"}} {tunnel.get("outgoing_bytes", 0)}')
        else:
            metrics.append(f'# Error: Failed to fetch IPSec data for {fw["name"]}')
    except Exception as e:
        metrics.append(f'# Error fetching IPSec status for {fw["name"]}: {e}')

    return metrics


@app.route("/metrics")
def metrics():
    """Prometheus scrape endpoint"""
    all_metrics = []
    for fw in FIREWALLS:
        all_metrics.extend(fetch_interface_stats(fw))
        all_metrics.extend(fetch_resource_metrics(fw))
        all_metrics.extend(fetch_uptime(fw))
        all_metrics.extend(fetch_ipsec_status(fw))
    return Response("\n".join(all_metrics), mimetype="text/plain")


if __name__ == "__main__":
    port = int(os.getenv("EXPORTER_PORT", 9200))
    app.run(host="0.0.0.0", port=port)
