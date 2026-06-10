"""
MySQL Prometheus Exporter
Exports: slow queries, replication lag, connection usage, InnoDB buffer pool,
query cache, table locks, thread states.
"""

import time
import logging
import yaml
from prometheus_client import start_http_server, Gauge, Counter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

try:
    import mysql.connector
    MYSQL_AVAILABLE = True
except ImportError:
    MYSQL_AVAILABLE = False

# ── Metrics ───────────────────────────────────────────────────────────────────
MYSQL_UP               = Gauge("mysql_up",                          "MySQL is reachable")
MYSQL_UPTIME           = Gauge("mysql_global_status_uptime",        "MySQL uptime in seconds")
MYSQL_CONNECTIONS      = Gauge("mysql_global_status_threads_connected", "Current open connections")
MYSQL_MAX_CONNECTIONS  = Gauge("mysql_global_variables_max_connections","max_connections setting")
MYSQL_CONN_PCT         = Gauge("mysql_connection_usage_percent",    "Connections used as % of max")
MYSQL_SLOW_QUERIES     = Gauge("mysql_global_status_slow_queries",  "Total slow queries")
MYSQL_QUESTIONS        = Counter("mysql_global_status_questions_total", "Total questions/queries")
MYSQL_REPLICATION_LAG  = Gauge("mysql_slave_status_seconds_behind_master", "Replication lag seconds", ["master_host"])
MYSQL_REPLICATION_UP   = Gauge("mysql_slave_sql_running",           "1 if SQL thread is running",  ["master_host"])
INNODB_BUFFER_READS    = Gauge("mysql_innodb_buffer_pool_reads",    "InnoDB physical disk reads")
INNODB_BUFFER_REQUESTS = Gauge("mysql_innodb_buffer_pool_read_requests","InnoDB logical read requests")
INNODB_BUFFER_HIT_RATE = Gauge("mysql_innodb_buffer_pool_hit_rate_percent","InnoDB buffer pool hit rate %")
INNODB_PAGES_DIRTY     = Gauge("mysql_innodb_buffer_pool_pages_dirty","Dirty pages in buffer pool")
MYSQL_TABLE_LOCKS_WAIT = Gauge("mysql_global_status_table_locks_waited","Table lock waits")
MYSQL_DEADLOCKS        = Gauge("mysql_global_status_innodb_deadlocks","InnoDB deadlocks")
MYSQL_THREADS_RUNNING  = Gauge("mysql_global_status_threads_running","Threads currently running")
MYSQL_ABORTED_CONN     = Gauge("mysql_global_status_aborted_connects","Aborted connection attempts")

GLOBAL_STATUS_KEYS = {
    "Uptime", "Threads_connected", "Threads_running", "Slow_queries",
    "Table_locks_waited", "Aborted_connects",
    "Innodb_buffer_pool_reads", "Innodb_buffer_pool_read_requests",
    "Innodb_buffer_pool_pages_dirty", "Innodb_deadlocks", "Questions",
}

def get_status(conn) -> dict:
    cur = conn.cursor()
    cur.execute("SHOW GLOBAL STATUS")
    rows = {row[0]: row[1] for row in cur.fetchall()}
    cur.close()
    return rows

def get_variables(conn) -> dict:
    cur = conn.cursor()
    cur.execute("SHOW GLOBAL VARIABLES LIKE 'max_connections'")
    rows = {row[0]: row[1] for row in cur.fetchall()}
    cur.close()
    return rows

def get_slave_status(conn) -> list:
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SHOW SLAVE STATUS")
        rows = cur.fetchall()
    except Exception:
        rows = []
    cur.close()
    return rows

def collect(conn):
    try:
        status = get_status(conn)
        variables = get_variables(conn)

        MYSQL_UP.set(1)
        MYSQL_UPTIME.set(float(status.get("Uptime", 0)))
        MYSQL_CONNECTIONS.set(float(status.get("Threads_connected", 0)))
        MYSQL_THREADS_RUNNING.set(float(status.get("Threads_running", 0)))
        MYSQL_SLOW_QUERIES.set(float(status.get("Slow_queries", 0)))
        MYSQL_TABLE_LOCKS_WAIT.set(float(status.get("Table_locks_waited", 0)))
        MYSQL_ABORTED_CONN.set(float(status.get("Aborted_connects", 0)))
        MYSQL_DEADLOCKS.set(float(status.get("Innodb_deadlocks", 0)))

        max_conn = float(variables.get("max_connections", 151))
        MYSQL_MAX_CONNECTIONS.set(max_conn)
        conn_pct = float(status.get("Threads_connected", 0)) / max_conn * 100
        MYSQL_CONN_PCT.set(round(conn_pct, 2))

        reads    = float(status.get("Innodb_buffer_pool_reads", 1))
        requests = float(status.get("Innodb_buffer_pool_read_requests", 1))
        INNODB_BUFFER_READS.set(reads)
        INNODB_BUFFER_REQUESTS.set(requests)
        INNODB_PAGES_DIRTY.set(float(status.get("Innodb_buffer_pool_pages_dirty", 0)))
        hit_rate = (1 - reads / requests) * 100 if requests > 0 else 100
        INNODB_BUFFER_HIT_RATE.set(round(hit_rate, 2))

        # Replication
        for slave in get_slave_status(conn):
            master = slave.get("Master_Host", "unknown")
            lag = slave.get("Seconds_Behind_Master")
            MYSQL_REPLICATION_LAG.labels(master_host=master).set(float(lag) if lag is not None else -1)
            sql_running = 1 if slave.get("Slave_SQL_Running") == "Yes" else 0
            MYSQL_REPLICATION_UP.labels(master_host=master).set(sql_running)

    except Exception as e:
        MYSQL_UP.set(0)
        log.error("MySQL collect error: %s", e)
        raise

def main():
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    port     = cfg.get("listen_port", 9104)
    interval = cfg.get("scrape_interval", 15)
    db_cfg   = cfg.get("mysql", {})

    start_http_server(port)
    log.info("MySQL exporter started on :%d", port)

    if not MYSQL_AVAILABLE:
        log.error("Install: pip install mysql-connector-python")
        return

    conn = None
    while True:
        try:
            if conn is None:
                conn = mysql.connector.connect(
                    host=db_cfg.get("host", "localhost"),
                    port=db_cfg.get("port", 3306),
                    user=db_cfg.get("user", "exporter"),
                    password=db_cfg.get("password", ""),
                    database="information_schema",
                    connection_timeout=5,
                )
                log.info("Connected to MySQL")
            collect(conn)
        except Exception as e:
            log.warning("Reconnecting: %s", e)
            MYSQL_UP.set(0)
            if conn:
                try: conn.close()
                except: pass
            conn = None
        time.sleep(interval)

if __name__ == "__main__":
    main()
