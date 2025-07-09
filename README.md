# Personal Website

A Flask-powered personal website featuring user authentication, dynamic content pages, server status monitoring via Discord webhooks, and offline support.
Website hosted on a Raspberry Pi and exposed via a Cloudflare Tunnel.  

Accessible at: [https://zubekanov.com](https://zubekanov.com).

Design document viewable on [Google Docs](https://docs.google.com/document/d/12hfty43L8W6g3G-6i6KKSfHclK3PIoREcdh7M0XXjBQ/edit?usp=sharing).

## Overview

This site runs entirely on an 8 GB Raspberry Pi 5 in my home network, with DNS and HTTPS termination handled by Cloudflare. All traffic is routed through a Cloudflare Tunnel back to the Pi, giving the appearance of a public IP without exposing the device directly.

Under the hood, the backend is a Flask application written in Python 3.11. It uses Jinja2 templates to assemble dynamic pages, and a PostgreSQL database to persist user accounts, verification tokens, password-reset requests and server metrics. User-facing routes (registration, login, email verification and password reset) live in `app/routes.py` and `app/user_management.py`, while layout logic and breadcrumb generation are factored into `app/layout_fetcher.py` and `app/breadcrumbs.py`.

The backend is written in Python using Flask and integrates a PostgreSQL database for storing user data and managing services. Static resources such as images, icons, and CSS files are served directly from the Raspberry Pi.

The frontend utilises basic HTML and CSS, with the focus of the project on managing resources server-side and minimising client-side load.

## Tech Stack

- **Backend:** Python 3.11, Flask  
- **Database:** PostgreSQL  
- **Frontend:** Jinja2 templates, vanilla JS, CSS  

