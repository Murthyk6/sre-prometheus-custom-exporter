"""
AWS Cost Prometheus Exporter
Uses AWS Cost Explorer API (boto3) to export daily and monthly spend
per service, linked account, and region as Prometheus gauges.
"""

import time
import logging
from datetime import datetime, timedelta, timezone
import yaml
from prometheus_client import start_http_server, Gauge

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

try:
    import boto3
    BOTO_AVAILABLE = True
except ImportError:
    BOTO_AVAILABLE = False

# ── Metrics ───────────────────────────────────────────────────────────────────
AWS_COST_DAILY     = Gauge("aws_cost_daily_usd",           "AWS daily cost in USD",          ["service", "account"])
AWS_COST_MONTHLY   = Gauge("aws_cost_monthly_usd",         "AWS month-to-date cost in USD",  ["service", "account"])
AWS_COST_FORECAST  = Gauge("aws_cost_monthly_forecast_usd","AWS monthly cost forecast USD",  ["account"])
AWS_BUDGET_ACTUAL  = Gauge("aws_budget_actual_spend_usd",  "Actual spend against budget",    ["budget_name"])
AWS_BUDGET_LIMIT   = Gauge("aws_budget_limit_usd",         "Budget limit in USD",            ["budget_name"])
AWS_COST_TOTAL     = Gauge("aws_cost_total_daily_usd",     "Total daily AWS spend USD")
AWS_COLLECT_ERRORS = Gauge("aws_cost_collect_errors_total","Cost collector error count")

def get_cost_by_service(client, start: str, end: str) -> dict:
    """Returns {(service, account): cost_usd}"""
    results = {}
    paginator = client.get_paginator("get_cost_and_usage") if hasattr(client, "get_paginator") else None

    resp = client.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        GroupBy=[
            {"Type": "DIMENSION", "Key": "SERVICE"},
            {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"},
        ],
    )

    for result_by_time in resp.get("ResultsByTime", []):
        for group in result_by_time.get("Groups", []):
            service = group["Keys"][0]
            account = group["Keys"][1]
            amount  = float(group["Metrics"]["UnblendedCost"]["Amount"])
            key = (service, account)
            results[key] = results.get(key, 0) + amount
    return results

def get_monthly_cost(client, account_id: str) -> dict:
    today = datetime.now(timezone.utc)
    start = today.replace(day=1).strftime("%Y-%m-%d")
    end   = (today + timedelta(days=1)).strftime("%Y-%m-%d")

    resp = client.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )

    results = {}
    for result in resp.get("ResultsByTime", []):
        for group in result.get("Groups", []):
            service = group["Keys"][0]
            amount  = float(group["Metrics"]["UnblendedCost"]["Amount"])
            results[service] = amount
    return results

def get_forecast(client) -> float:
    today = datetime.now(timezone.utc)
    start = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    end   = today.replace(day=1).replace(month=today.month % 12 + 1).strftime("%Y-%m-%d")
    try:
        resp = client.get_cost_forecast(
            TimePeriod={"Start": start, "End": end},
            Metric="UNBLENDED_COST",
            Granularity="MONTHLY",
        )
        return float(resp["Total"]["Amount"])
    except Exception:
        return 0.0

def collect(cfg: dict):
    try:
        session = boto3.Session(
            region_name=cfg.get("aws", {}).get("region", "us-east-1"),
            aws_access_key_id=cfg.get("aws", {}).get("access_key_id") or None,
            aws_secret_access_key=cfg.get("aws", {}).get("secret_access_key") or None,
        )
        ce = session.client("ce", region_name="us-east-1")
        sts = session.client("sts")
        account_id = sts.get_caller_identity()["Account"]

        today = datetime.now(timezone.utc)
        yesterday = (today - timedelta(days=1)).strftime("%Y-%m-%d")

        # Daily costs by service
        daily = get_cost_by_service(ce, yesterday, today.strftime("%Y-%m-%d"))
        total_daily = 0.0
        for (service, account), amount in daily.items():
            AWS_COST_DAILY.labels(service=service, account=account).set(round(amount, 4))
            total_daily += amount
        AWS_COST_TOTAL.set(round(total_daily, 4))

        # Monthly costs
        monthly = get_monthly_cost(ce, account_id)
        for service, amount in monthly.items():
            AWS_COST_MONTHLY.labels(service=service, account=account_id).set(round(amount, 4))

        # Forecast
        forecast = get_forecast(ce)
        AWS_COST_FORECAST.labels(account=account_id).set(round(forecast, 2))

        # Budgets
        try:
            budgets_client = session.client("budgets")
            resp = budgets_client.describe_budgets(AccountId=account_id)
            for b in resp.get("Budgets", []):
                name   = b["BudgetName"]
                limit  = float(b["BudgetLimit"]["Amount"])
                actual = float(b.get("CalculatedSpend", {}).get("ActualSpend", {}).get("Amount", 0))
                AWS_BUDGET_LIMIT.labels(budget_name=name).set(limit)
                AWS_BUDGET_ACTUAL.labels(budget_name=name).set(actual)
        except Exception as e:
            log.debug("Budgets not available: %s", e)

        log.info("Cost collection done — daily total: $%.2f  forecast: $%.2f", total_daily, forecast)

    except Exception as e:
        AWS_COLLECT_ERRORS.inc()
        log.error("AWS cost collect error: %s", e)

def main():
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    port     = cfg.get("listen_port", 9600)
    interval = cfg.get("scrape_interval", 3600)

    start_http_server(port)
    log.info("AWS cost exporter started on :%d", port)

    if not BOTO_AVAILABLE:
        log.error("boto3 not installed. Run: pip install boto3")
        return

    while True:
        collect(cfg)
        time.sleep(interval)

if __name__ == "__main__":
    main()
