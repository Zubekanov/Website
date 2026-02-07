# Production Deployment (Flask + Gunicorn + systemd + Cloudflare Tunnel)

This project is structured under `src/`, so Gunicorn should serve `wsgi:app` with working directory set to `src`.

## 1) One-time server setup

Assumed target paths:
- Repo: `/opt/Website_Dev`
- Virtualenv: `/opt/Website_Dev/.venv`
- Service user: `www-data`

Install dependencies:

```bash
cd /opt/Website_Dev
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r src/requirements.txt
```

`gunicorn` is required. Ensure it exists in `src/requirements.txt`.

## 2) Install systemd service

Copy service unit:

```bash
sudo cp /opt/Website_Dev/deploy/systemd/website.service /etc/systemd/system/website.service
sudo systemctl daemon-reload
sudo systemctl enable website.service
sudo systemctl start website.service
```

Check status/logs:

```bash
sudo systemctl --no-pager --full status website.service
sudo journalctl -u website.service -n 200 --no-pager
```

## 3) Cloudflare Tunnel origin mapping

Your Cloudflare ingress should target local Gunicorn bind:

```yaml
ingress:
  - hostname: zubekanov.com
    service: http://127.0.0.1:8000
  - hostname: www.zubekanov.com
    service: http://127.0.0.1:8000
  - service: http_status:404
```

If needed, use `deploy/cloudflared/config.example.yml` as a template.

Validate locally on server:

```bash
curl -I http://127.0.0.1:8000/
```

Validate tunnel/service:

```bash
sudo systemctl --no-pager --full status cloudflared
sudo journalctl -u cloudflared -n 200 --no-pager
```

## 4) Standard redeploy workflow

Use:

```bash
/opt/Website_Dev/deploy/scripts/redeploy.sh
```

It does:
1. `git pull --ff-only`
2. `pip install -r src/requirements.txt`
3. `gunicorn --check-config`
4. `systemctl restart website.service`
5. status output

No Cloudflare tunnel change is needed unless your local bind/port changes.

## 5) Gunicorn settings used and why

Config file: `deploy/gunicorn.conf.py`

- `bind = 127.0.0.1:8000` for private local origin behind tunnel
- `workers = 1` because `create_app()` starts background threads; multiple workers would duplicate them
- `threads = 8` for request concurrency without multiplying background workers
- journald logging through stdout/stderr for simple ops

## 6) Optional improvements

- Health endpoint: add `/healthz` route returning 200 and basic dependency status.
- Readiness checks: in deploy pipeline, hit `http://127.0.0.1:8000/healthz` post-restart.
- Static files: current Flask static serving is fine for modest traffic; add Nginx only if you need buffering/rate limits/custom caching.
- Horizontal scaling: if you remove in-process background thread starts from `create_app()`, you can safely increase Gunicorn workers.
