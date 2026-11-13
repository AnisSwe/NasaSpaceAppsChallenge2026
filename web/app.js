let currentMode = "harmonised";
let staticBundle = null;

const VAL_KEYS = { harmonised: "harmonised_count", calibrated: "harmonised_calibrated", raw: "raw_count" };
const SENSOR_BREAK = "2012-01-01";

function setMode(mode) {
  currentMode = mode;
  document.getElementById("btnHarm").classList.toggle("active", mode === "harmonised");
  document.getElementById("btnCal").classList.toggle("active", mode === "calibrated");
  document.getElementById("btnRaw").classList.toggle("active", mode === "raw");
  refreshDashboard();
}

async function loadData(country, targetDate) {
  try {
    const [mRes, aRes, dRes] = await Promise.all([
      fetch("/api/metrics"),
      fetch(`/api/anomaly?country=${encodeURIComponent(country)}&target_date=${targetDate}&mode=${currentMode}`),
      fetch(`/api/daily/${encodeURIComponent(country)}`)
    ]);
    if (mRes.ok && aRes.ok && dRes.ok) {
      return { metrics: await mRes.json(), anomaly: await aRes.json(), daily: await dRes.json() };
    }
  } catch (e) {}

  // Static fallback: served from the web/ folder, so the fixtures sit one level up.
  if (!staticBundle) {
    const res = await fetch("/demo_fixtures/web_static_cache.json");
    staticBundle = await res.json();
  }
  const daily = staticBundle.daily[country] || [];
  return { metrics: staticBundle.metrics, anomaly: null, daily };
}

async function refreshDashboard() {
  const country = document.getElementById("countrySelect").value;
  const targetDate = document.getElementById("dateInput").value;
  const { metrics, anomaly, daily } = await loadData(country, targetDate);

  const cm = (metrics.by_country && metrics.by_country[country]) || metrics;
  const rHarm = cm.r_harmonised == null ? "n/a" : cm.r_harmonised.toFixed(4);
  document.getElementById("metricsRow").innerHTML = `
    <div class="metric-card"><div class="metric-label">Raw MODIS vs VIIRS (r)</div><div class="metric-value">${cm.r_raw == null ? "n/a" : cm.r_raw.toFixed(4)}</div></div>
    <div class="metric-card"><div class="metric-label">Harmonised MODIS vs VIIRS (r)</div><div class="metric-value">${rHarm}</div></div>
    <div class="metric-card"><div class="metric-label">Sensor Overlap Days</div><div class="metric-value">${cm.overlap_days.toLocaleString()}</div></div>
    <div class="metric-card"><div class="metric-label">Spatial Grid &amp; Confidence</div><div class="metric-value">${metrics.cell_size_km} km | &ge;${metrics.confidence_min}</div></div>
  `;

  const anomalyBox = document.getElementById("anomalyBox");
  if (anomaly && anomaly.status === "OK") {
    const caveat = anomaly.baseline_mixes_sensor_eras
      ? ` <em>Caution: this baseline mixes MODIS-era and VIIRS-era counts. Switch to Calibrated mode.</em>`
      : "";
    anomalyBox.innerHTML =
      `<strong>Is this unusual? ${anomaly.verdict}</strong> — On <strong>${anomaly.date}</strong> in <strong>${country.replace("_", " ")}</strong>, activity was <strong>${anomaly.current_value.toLocaleString()}</strong> (<strong>${anomaly.percentile}th percentile</strong> vs. &plusmn;7-day baseline median ${anomaly.baseline_median}, 90th pct ${anomaly.baseline_p90}).${caveat}`;
  } else if (anomaly && anomaly.status === "MODE_UNAVAILABLE") {
    anomalyBox.textContent = "Calibrated mode is not available: run src/compute/calibrate.py to generate it.";
  }

  const valKey = VAL_KEYS[currentMode] || "harmonised_count";
  const years = [...new Set(daily.map(d => d.year))].sort();
  const doys = Array.from({ length: 365 }, (_, i) => i + 1);
  const zGrid = years.map(y => {
    const row = new Array(365).fill(0);
    daily.filter(d => d.year === y).forEach(d => { if (d.doy >= 1 && d.doy <= 365) row[d.doy - 1] = d[valKey] ?? 0; });
    return row;
  });

  Plotly.newPlot("calendarChart", [{
    z: zGrid, x: doys, y: years, type: "heatmap", colorscale: "YlOrRd"
  }], { paper_bgcolor: "#181c26", plot_bgcolor: "#181c26", font: { color: "#f3f4f6" }, margin: { t: 10, l: 45, r: 10, b: 35 } }, { responsive: true });

  const breakShape = [{
    type: "line", x0: SENSOR_BREAK, x1: SENSOR_BREAK, y0: 0, y1: 1, yref: "paper",
    line: { color: "#fbbf24", width: 1.5, dash: "dash" }
  }];
  const breakNote = [{
    x: SENSOR_BREAK, y: 1, yref: "paper", text: "VIIRS begins", showarrow: false,
    xanchor: "left", font: { color: "#fbbf24", size: 11 }
  }];

  Plotly.newPlot("seriesChart", [{
    x: daily.map(d => d.date), y: daily.map(d => d[valKey] ?? null), type: "scatter", mode: "lines",
    line: { color: "#fd8d3c", width: 1.2 }
  }], {
    paper_bgcolor: "#181c26", plot_bgcolor: "#181c26", font: { color: "#f3f4f6" },
    margin: { t: 10, l: 45, r: 10, b: 35 }, shapes: breakShape, annotations: breakNote
  }, { responsive: true });
}

document.getElementById("countrySelect").addEventListener("change", refreshDashboard);
document.getElementById("dateInput").addEventListener("change", refreshDashboard);
refreshDashboard();
