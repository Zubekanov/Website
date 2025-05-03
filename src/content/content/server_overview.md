<div class="grid">
    <div class="box">
        <h3>Server Statistics</h3>
            <p>This page currently shows server statistics over the previous hour.</p>
            <p>All metrics collected from the server are stored, although the website currently does not support displaying past the current hour. Additionally, CPU temp is the only interesting statistic to view over time as the other metrics are relatively static.</p>
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
</div>
