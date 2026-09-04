"""Gunicorn configuration for SPSE Crawler production deployment."""

import multiprocessing
import os

# Server socket
bind = "0.0.0.0:8000"
backlog = 2048

# Worker processes
workers = int(os.environ.get("GUNICORN_WORKERS", min(multiprocessing.cpu_count() * 2 + 1, 8)))
worker_class = "sync"
worker_connections = 1000
timeout = 120
keepalive = 5
graceful_timeout = 30

# Restart workers after this many requests (prevents memory leaks)
max_requests = 1000
max_requests_jitter = 50

# Logging
accesslog = os.environ.get("GUNICORN_ACCESS_LOG", "-")
errorlog = os.environ.get("GUNICORN_ERROR_LOG", "-")
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "spse-crawler"

# Security
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 8190

forwarded_allow_ips = "*"

# Server mechanics
preload_app = True
daemon = False
tmp_upload_dir = None

# SSL (uncomment if using SSL termination at Gunicorn level)
# certfile = "/path/to/cert.pem"
# keyfile = "/path/to/key.pem"
