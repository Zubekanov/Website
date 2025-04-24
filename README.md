# Source Code for my Personal Website

Website hosted on a Raspberry Pi and exposed via a Cloudflare Tunnel.  
Accessible at: [https://zubekanov.com](https://zubekanov.com).
Design document viewable on [Google Docs](https://docs.google.com/document/d/12hfty43L8W6g3G-6i6KKSfHclK3PIoREcdh7M0XXjBQ/edit?usp=sharing).

## Offline Cloudflare Worker

This project includes a lightweight Cloudflare Worker that serves a static HTML fallback page whenever the main site is offline.

The source code for the Worker lives in `src/offline/`, and the fallback page is always available at [https://offline.zubekanov.com](https://offline.zubekanov.com), even when the main site is up.
