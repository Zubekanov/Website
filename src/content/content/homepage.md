<h1 style="text-align: center;">Joseph Wong</h1>

<p style="text-align: center;">
    <img src="/static/images/profile.png" alt="" style="width:200px; border-radius: 50%;">
</p>

<p style="text-align: center; font-size: 1.2em; line-height: 1.6;">
    Welcome to my personal website!
</p>

---

## About This Website

The GitHub repository for this project is available at [github.com/Zubekanov/Website](https://github.com/Zubekanov/Website).

The site is self-hosted on an 8 GB Raspberry Pi 5, with DNS handled by Cloudflare. There’s a placeholder Cloudflare Worker in `src/offline/` intended to serve a static fallback page at [offline.zubekanov.com](https://offline.zubekanov.com) when the main site goes down, but is not currently functional.

You can monitor CPU, memory and uptime on the built-in **Server Stats** page at `/server`, or by clicking the uptime icon at the top of the page.

Under the hood, the backend is Python/Flask with a PostgreSQL database for user data and service management. All static assets (images, icons, CSS) are served directly from the Pi.

The frontend uses plain HTML and CSS, prioritising minimal client-side load and robust server-side resource handling.

User accounts and email verification are supported. They may be more useful in the future, but currently are just proof of concept for database management.


---

## About Me

I'm currently completing a double degree in Computer Science and Advanced Mathematics at UNSW, and looking to apply my skills in a graduate role.

---

## Contact

You can find me here:

- 📧 [Email](mailto:josephwong17@gmail.com)
- 💼 [LinkedIn](https://www.linkedin.com/in/joseph-wong-b77461248/)
- 🛠 [GitHub](https://github.com/Zubekanov)
