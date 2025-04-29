<div class="grid">
    <div class="box">
        <h3>Server Overview</h3>
            <p>Welcome to my personal server status page! Here you can monitor in real-time:</p>
            <ul>
            <li><strong>CPU Temp &amp; Utilisation</strong> – current load and temperature</li>
            <li><strong>RAM &amp; Disk Usage</strong> – memory and storage consumption over the last hour</li>
            </ul>
            <p>Metrics refresh every 5 seconds.</p>
    </div>
    <div class="chart-card">
        <h3>CPU Temperature (°C)</h3>
        <canvas id="cpu_temp"></canvas>
    </div>
    <div class="chart-card">
        <h3>CPU Usage (%)</h3>
        <canvas id="cpu_percent"></canvas>
    </div>
    <div class="chart-card">
        <h3>RAM Usage (GiB)</h3>
        <canvas id="ram_used"></canvas>
    </div>
    <div class="chart-card">
        <h3>Disk Usage (GiB)</h3>
        <canvas id="disk_used"></canvas>
    </div>
    <div class="chart-card">
        <h3>Placeholder Chart</h3>
        <canvas id="chart5"></canvas>
    </div>
</div>
