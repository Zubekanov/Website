// worker.js
export default {
	async fetch(request, env, ctx) {
		const url = new URL(request.url);
		const subdomain = url.hostname.split(".")[0];

		const kvKey = `downSince:${subdomain}`;
		const now = new Date().toISOString();

		// Check KV to see if we've already recorded a down time for this subdomain
		let downSince = await env.OFFLINE_STATS.get(kvKey);

		if (!downSince) {
			downSince = now;
			await env.OFFLINE_STATS.put(kvKey, downSince);
		}

		const html = `
<!DOCTYPE html>
<html lang="en">
<head>
	<meta charset="UTF-8" />
	<title>Site Offline</title>
	<style>
		body {
			font-family: sans-serif;
			background-color: #f5f5f5;
			color: #222;
			max-width: 600px;
			margin: 80px auto;
			padding: 2rem;
			border: 1px solid #ccc;
			background: #fff;
		}
		h1 {
			font-size: 1.8em;
			margin-bottom: 0.5em;
		}
		code {
			background: #eee;
			padding: 2px 4px;
			border-radius: 3px;
		}
		a {
			color: #0070cc;
			text-decoration: none;
		}
		a:hover {
			text-decoration: underline;
		}
		@media (prefers-color-scheme: dark) {
			body {
				background-color: #1e1e1e;
				color: #eaeaea;
				border-color: #444;
			}
			code {
				background: #333;
			}
			a {
				color: #66bfff;
			}
		}
	</style>
	<script>
		document.addEventListener("DOMContentLoaded", function () {
			const ts = new Date('${downSince}');
			document.getElementById("since-time").textContent = ts.toLocaleString();
		});
	</script>
</head>
<body>
	<h1>Website currently down.</h1>
	<p>This site is currently offline due to a power or internet outage, or because I broke something in the backend.</p>
	<p>In the meantime, my details are below:</p>
	<ul>
		<li><strong>Email:</strong> <a href="mailto:josephwong17@gmail.com">josephwong17@gmail.com</a></li>
		<li><strong>GitHub:</strong> <a href="https://github.com/Zubekanov" target="_blank">github.com/Zubekanov</a></li>
		<li><strong>LinkedIn:</strong> <a href="https://www.linkedin.com/in/joseph-wong-b77461248/" target="_blank">linkedin.com/in/joseph-wong</a></li>
	</ul>
	<p class="down-since">This page has been live since: <strong id="since-time">...</strong></p>
	<p>The site (hopefully) will be back online soon.</p>
</body>
</html>`;

		return new Response(html, {
			headers: { "Content-Type": "text/html; charset=utf-8" },
			status: 200
		});
	}
};
