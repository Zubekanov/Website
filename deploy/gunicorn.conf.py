# Cloudflare Tunnel should point to this local origin.
bind = "127.0.0.1:8000"

# Your app starts background threads in create_app(); multiple workers would
# duplicate those threads. Keep one worker and use threads for concurrency.
workers = 1
threads = 8
worker_class = "gthread"

# Timeouts tuned for web requests behind Cloudflare.
timeout = 60
graceful_timeout = 30
keepalive = 5

# Let systemd/journald handle logs.
accesslog = "-"
errorlog = "-"
loglevel = "info"
capture_output = True

# Restart workers periodically to limit long-lived memory growth.
max_requests = 1000
max_requests_jitter = 100

# Security/robustness
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190
