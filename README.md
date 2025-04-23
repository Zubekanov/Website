# Source Code for my Personal Website

Website hosted on a Raspberry Pi and exposed via a Cloudflare Tunnel.  
Accessible at: [https://zubekanov.com](https://zubekanov.com)

## Offline Fallback Worker

This project includes a lightweight Cloudflare Worker that serves a static HTML fallback page whenever the main site is offline—typically due to a power or internet outage, or while backend work is in progress.

The source code for the Worker lives in `src/offline/`, and the fallback page is always available at [https://offline.zubekanov.com](https://offline.zubekanov.com), even when the main site is up.
