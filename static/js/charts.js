/*
 * Chart.js rendering for the dashboard, analytics and admin pages.
 * Every number comes from a <script type="application/json"> block that the
 * server filled from MongoDB; nothing here is hard-coded.
 */
(function () {
  "use strict";
  if (!window.Chart) return;

  const INDIGO = "#4f3ce6", VIOLET = "#7c5cfa";
  const SERIES = ["#4f3ce6", "#7c5cfa", "#0891b2", "#d97706", "#d63b4e", "#0e9f6e", "#64748b", "#c026d3", "#65a30d", "#0f766e"];
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 12.5;
  Chart.defaults.color = "#7a7699";
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.tooltip.backgroundColor = "#1b1740";
  Chart.defaults.plugins.tooltip.padding = 10;
  const grid = { color: "#efeef6" };

  function json(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  function lineChart(canvas, labels, datasets) {
    return new Chart(canvas, {
      type: "line",
      data: { labels, datasets },
      options: {
        maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: datasets.length > 1, position: "bottom" } },
        scales: { y: { beginAtZero: true, ticks: { precision: 0 }, grid }, x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } } },
      },
    });
  }
  const lineSeries = (label, data, color, fill) => ({
    label, data, borderColor: color, backgroundColor: fill ? "rgba(79,60,230,.08)" : color, fill: !!fill,
    tension: .3, pointRadius: data.length > 40 ? 0 : 3, pointHoverRadius: 5, borderWidth: 2,
  });

  function barChart(canvas, labels, values, { horizontal = false, colors = INDIGO, label = "Responses", pct = null } = {}) {
    return new Chart(canvas, {
      type: "bar",
      data: { labels, datasets: [{ label, data: values, backgroundColor: colors, borderRadius: 6, maxBarThickness: 36 }] },
      options: {
        maintainAspectRatio: false, indexAxis: horizontal ? "y" : "x",
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => ` ${ctx.raw} ${label.toLowerCase()}${pct ? ` (${pct[ctx.dataIndex]}%)` : ""}` } },
        },
        scales: {
          [horizontal ? "x" : "y"]: { beginAtZero: true, ticks: { precision: 0 }, grid },
          [horizontal ? "y" : "x"]: { grid: { display: false }, ticks: { callback(value) { const l = this.getLabelForValue(value); return l.length > 22 ? `${l.slice(0, 21)}…` : l; } } },
        },
      },
    });
  }

  // ---------------- Dashboard / analytics trend
  const trendCanvas = document.getElementById("trend-chart");
  const trend = json("trend-data") || (json("chart-data") || {}).trend;
  if (trendCanvas && trend) {
    lineChart(trendCanvas, trend.map((d) => d.label), [lineSeries("Responses", trend.map((d) => d.count), INDIGO, true)]);
  }

  // ---------------- Per-question analytics charts
  const chartData = json("chart-data");
  if (chartData) {
    const byQid = Object.fromEntries(chartData.questions.map((q) => [q.qid, q]));
    document.querySelectorAll("canvas[data-chart]").forEach((canvas) => {
      const q = byQid[canvas.dataset.chart];
      if (!q) return;
      if (q.kind === "choice") {
        new Chart(canvas, {
          type: "doughnut",
          data: { labels: q.options.map((o) => o.label), datasets: [{ data: q.options.map((o) => o.count), backgroundColor: SERIES, borderColor: "#fff", borderWidth: 2 }] },
          options: { maintainAspectRatio: false, cutout: "62%",
            plugins: { legend: { position: "right", labels: { boxWidth: 9 } },
              tooltip: { callbacks: { label: (ctx) => ` ${ctx.label}: ${ctx.raw} (${q.options[ctx.dataIndex].pct}% of answerers)` } } } },
        });
      } else if (q.kind === "multi") {
        barChart(canvas, q.options.map((o) => o.label), q.options.map((o) => o.count), { horizontal: true, colors: VIOLET, label: "Selections", pct: q.options.map((o) => o.pct) });
      } else if (q.kind === "scale") {
        barChart(canvas, q.options.map((o) => o.label), q.options.map((o) => o.count), { colors: q.options.map((_, i) => (i >= q.options.length - 2 ? VIOLET : INDIGO)), pct: q.options.map((o) => o.pct) });
      } else if (q.kind === "numeric") {
        barChart(canvas, q.histogram.map((b) => b.label), q.histogram.map((b) => b.count), { label: "Answers" });
      } else if (q.kind === "date") {
        barChart(canvas, q.by_month.map((m) => m.label), q.by_month.map((m) => m.count), { label: "Answers" });
      }
    });
  }

  // ---------------- AI rewrite of the summary (optional)
  const aiButton = document.getElementById("ai-summary-btn");
  if (aiButton) {
    aiButton.addEventListener("click", async () => {
      const box = document.getElementById("ai-summary");
      aiButton.disabled = true;
      box.classList.remove("d-none", "alert-danger");
      box.textContent = "Asking the AI service…";
      try {
        const response = await fetch(aiButton.dataset.url, { method: "POST", credentials: "same-origin",
          headers: { "X-CSRFToken": window.PollPulse.getCookie("csrftoken") } });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "The AI service did not respond.");
        box.replaceChildren();
        const strong = document.createElement("strong");
        strong.textContent = `AI-written summary (${payload.model}): `;
        box.append(strong, document.createTextNode(payload.summary));
      } catch (error) {
        box.classList.add("alert-danger");
        box.textContent = error.message;
      } finally {
        aiButton.disabled = false;
      }
    });
  }

  // ---------------- Admin platform activity
  const activity = json("activity-data");
  const activityCanvas = document.getElementById("activity-chart");
  if (activity && activityCanvas) {
    lineChart(activityCanvas, activity.labels, [
      lineSeries("Responses", activity.responses, INDIGO, true),
      lineSeries("New surveys", activity.surveys, VIOLET),
      lineSeries("New users", activity.registrations, "#0891b2"),
    ]);
  }
})();
