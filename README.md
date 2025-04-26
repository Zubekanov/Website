# Personal Website

Website hosted on a Raspberry Pi and exposed via a Cloudflare Tunnel.  

Accessible at: [https://zubekanov.com](https://zubekanov.com).

Design document viewable on [Google Docs](https://docs.google.com/document/d/12hfty43L8W6g3G-6i6KKSfHclK3PIoREcdh7M0XXjBQ/edit?usp=sharing).

## Overview

The website is self-hosted with an 8gb Raspberry Pi 5, with DNS resolution provided by Cloudflare.

The backend is written in Python using Flask and integrates a PostgreSQL database for storing user data and managing services. Static resources such as images, icons, and CSS files are served directly from the Raspberry Pi.

The frontend utilises basic HTML and CSS, with the focus of the project on managing resources server-side and minimising client-side load.

## Offline Cloudflare Worker

This project includes a lightweight Cloudflare Worker that serves a static HTML fallback page whenever the main site is offline.

The source code for the Worker lives in `src/offline/`, and the fallback page is always available at [https://offline.zubekanov.com](https://offline.zubekanov.com), even when the main site is up.
