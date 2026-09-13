(() => {
  "use strict";

  const canvas = document.getElementById("rating-chart");
  const source = document.getElementById("rating-chart-data");
  const panel = document.getElementById("rating-chart-panel");
  if (!canvas || !source || !panel || typeof Chart === "undefined") return;

  const data = JSON.parse(source.textContent);
  const dateFormat = new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
  const labels = [
    "Starting rating",
    ...data.labels.map((timestamp) => dateFormat.format(new Date(timestamp))),
  ];
  panel.hidden = false;

  new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: "ELO rating",
        data: [1000, ...data.ratings],
        borderColor: "#234c3b",
        backgroundColor: "rgba(35, 76, 59, 0.07)",
        pointBackgroundColor: "#ad481c",
        pointBorderColor: "#fffdf8",
        pointBorderWidth: 2,
        pointRadius: 4,
        pointHoverRadius: 6,
        borderWidth: 2,
        fill: true,
        tension: 0,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (context) => `ELO: ${context.parsed.y.toLocaleString(undefined, {
              maximumFractionDigits: 0,
            })}`,
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { maxTicksLimit: 5, maxRotation: 0, color: "#586359" },
          title: { display: true, text: "Games in playing order", color: "#586359" },
        },
        y: {
          suggestedMin: 968,
          suggestedMax: 1032,
          grid: { color: "rgba(35, 76, 59, 0.10)" },
          ticks: { precision: 0, color: "#586359" },
          title: { display: true, text: "ELO rating", color: "#586359" },
        },
      },
    },
  });
})();
