import argparse
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from data.db import DEFAULT_DB_PATH, connect, fetch_completed_counts

PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Traffic monitor</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    body { background: #111827; color: #f3f4f6; margin: 0; }
    main { margin: 0 auto; max-width: 1100px; padding: 32px 20px; }
    h1 { font-size: 1.5rem; font-weight: 600; margin: 0 0 6px; }
    p { color: #9ca3af; margin: 0 0 24px; }
    .panel { background: #1f2937; border: 1px solid #374151; border-radius: 12px; padding: 20px; }
    .meta { color: #9ca3af; font-size: .875rem; margin-bottom: 12px; }
    canvas { display: block; height: 420px; max-width: 100%; width: 100%; }
    .legend { display: flex; gap: 18px; margin-top: 14px; }
    .legend span::before { background: var(--color); border-radius: 50%; content: ""; display: inline-block; height: 9px; margin-right: 7px; width: 9px; }
    .empty { color: #9ca3af; padding: 80px 0; text-align: center; }
  </style>
</head>
<body>
  <main>
    <h1>Traffic monitor</h1>
    <section class="panel">
      <div class="meta" id="status">Loading…</div>
      <div id="empty" class="empty" hidden>No completed minute data yet.</div>
      <canvas id="chart" aria-label="Vehicle counts per minute" role="img"></canvas>
      <div class="legend">
        <span style="--color:#60a5fa">Cars</span>
        <span style="--color:#34d399">Bikes</span>
        <span style="--color:#fbbf24">Buses</span>
      </div>
    </section>
  </main>
  <script>
    const canvas = document.getElementById("chart");
    const status = document.getElementById("status");
    const empty = document.getElementById("empty");
    const series = [
      { key: "car", color: "#60a5fa" },
      { key: "bike", color: "#34d399" },
      { key: "bus", color: "#fbbf24" }
    ];

    function draw(buckets) {
      empty.hidden = buckets.length !== 0;
      canvas.hidden = buckets.length === 0;
      if (!buckets.length) return;
      const dpr = window.devicePixelRatio || 1;
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      const ctx = canvas.getContext("2d");
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, width, height);
      const pad = { top: 20, right: 18, bottom: 38, left: 42 };
      const chartWidth = width - pad.left - pad.right;
      const chartHeight = height - pad.top - pad.bottom;
      const max = Math.max(1, ...buckets.flatMap(b => series.map(s => b[s.key])));
      const x = i => pad.left + (buckets.length === 1 ? chartWidth / 2 : i * chartWidth / (buckets.length - 1));
      const y = value => pad.top + chartHeight - value / max * chartHeight;
      ctx.font = "12px system-ui";
      ctx.strokeStyle = "#374151";
      ctx.fillStyle = "#9ca3af";
      ctx.lineWidth = 1;
      for (let i = 0; i <= 4; i++) {
        const value = Math.round(max * i / 4);
        const lineY = y(value);
        ctx.beginPath(); ctx.moveTo(pad.left, lineY); ctx.lineTo(width - pad.right, lineY); ctx.stroke();
        ctx.fillText(value, 8, lineY + 4);
      }
      series.forEach(s => {
        ctx.strokeStyle = s.color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        buckets.forEach((bucket, i) => i ? ctx.lineTo(x(i), y(bucket[s.key])) : ctx.moveTo(x(i), y(bucket[s.key])));
        ctx.stroke();
        ctx.fillStyle = s.color;
        buckets.forEach((bucket, i) => { ctx.beginPath(); ctx.arc(x(i), y(bucket[s.key]), 3, 0, Math.PI * 2); ctx.fill(); });
      });
      ctx.fillStyle = "#9ca3af";
      buckets.forEach((bucket, i) => {
        if (buckets.length <= 8 || i === 0 || i === buckets.length - 1 || i % Math.ceil(buckets.length / 8) === 0) {
          ctx.fillText(new Date(bucket.timestamp * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }), x(i) - 17, height - 12);
        }
      });
    }

    async function refresh() {
      try {
        const response = await fetch("/dashboard/data", { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        draw(data.buckets);
        status.textContent = `Updated ${new Date().toLocaleTimeString()} · ${data.buckets.length} completed minutes`;
      } catch (error) {
        status.textContent = `Unable to load data: ${error.message}`;
      }
    }
    window.addEventListener("resize", refresh);
    refresh();
    setInterval(refresh, 60000);
  </script>
</body>
</html>"""

app = FastAPI(title="Traffic dashboard")
app.state.db_path = DEFAULT_DB_PATH


def format_datetime(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    return PAGE


@app.get("/dashboard/data")
def dashboard_data(request: Request) -> dict:
    conn = connect(request.app.state.db_path)
    try:
        rows = fetch_completed_counts(conn)
    finally:
        conn.close()
    return {
        "buckets": [
            {
                "timestamp": timestamp,
                "date": format_datetime(timestamp),
                "car": car,
                "bike": bike,
                "bus": bus,
            }
            for timestamp, car, bike, bus in rows
        ]
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Traffic monitoring dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    app.state.db_path = args.db
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
