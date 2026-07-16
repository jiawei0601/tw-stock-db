"""從 sector_flow_weekly 匯出一份獨立的靜態 HTML 動畫（板塊資金流動週度動畫），
不需要伺服器、瀏覽器打開即可播放。只取活動量最大的 N 個板塊（預設 20），避免
單一畫面塞太多長條看不清楚；可用 --top-n 調整。

用法：python export_sector_flow_animation.py [--db-path PATH] [--top-n 20] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "tw_stocks.db"
DEFAULT_OUT_PATH = Path(__file__).parent / "analysis" / "sector-flow-weekly-animation.html"

_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<title>板塊資金流動週度動畫</title>
<style>
  html {{ color-scheme: light; }}
  body {{ font-family: "Microsoft JhengHei", sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem;
          background: #ffffff; color: #1a1a1a; }}
  button, select {{ font-size: 14px; padding: 6px 12px; }}
  #weekLabel {{ font-weight: 500; }}
</style>
</head><body>
<h1 style="font-size:18px;">板塊資金流動週度動畫（每 5 個交易日一組，三大法人淨買賣超，單位：億股）</h1>
<div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;flex-wrap:wrap;">
  <button id="playBtn">播放</button>
  <span style="font-size:13px;color:#666;">速度</span>
  <select id="speedSel">
    <option value="600">正常</option>
    <option value="250">快</option>
    <option value="1200">慢</option>
  </select>
  <span id="weekLabel" style="margin-left:auto;"></span>
</div>
<input type="range" id="weekSlider" min="0" max="{max_week}" value="0" step="1" style="width:100%;margin-bottom:12px;" />
<div style="display:flex;gap:16px;font-size:12px;color:#666;margin-bottom:8px;">
  <span><span style="display:inline-block;width:10px;height:10px;background:#378ADD;"></span> 淨流入</span>
  <span><span style="display:inline-block;width:10px;height:10px;background:#D85A30;"></span> 淨流出</span>
</div>
<div style="position:relative;width:100%;height:560px;">
  <canvas id="raceChart"></canvas>
</div>
<div style="display:flex;gap:24px;margin-top:8px;padding-top:12px;border-top:1px solid #ddd;font-size:14px;">
  <span>淨流入合計：<b id="sumIn" style="color:#186bb5;"></b> 億股</span>
  <span>淨流出合計：<b id="sumOut" style="color:#b5401e;"></b> 億股</span>
  <span>淨額：<b id="sumNet"></b> 億股</span>
</div>
<p style="font-size:12px;color:#888;margin-top:4px;">合計為圖上顯示的 {top_n} 個板塊加總，非全市場 34 個板塊。</p>
<h2 style="font-size:14px;margin-top:20px;">當週大盤加權指數（TAIEX）走勢</h2>
<div style="position:relative;width:100%;height:180px;">
  <canvas id="idxChart"></canvas>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>
const DATA = {data_json};

const ctx = document.getElementById('raceChart');
const chart = new Chart(ctx, {{
  type: 'bar',
  data: {{
    labels: DATA.sectors,
    datasets: [{{
      data: DATA.weeks[0].v,
      backgroundColor: DATA.weeks[0].v.map(v => v >= 0 ? '#378ADD' : '#D85A30'),
      borderRadius: 4,
      barThickness: 18,
    }}]
  }},
  options: {{
    indexAxis: 'y',
    responsive: true,
    maintainAspectRatio: false,
    animation: {{ duration: 350 }},
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ min: {x_min}, max: {x_max}, ticks: {{ color: '#898781' }} }},
      y: {{ ticks: {{ color: '#0b0b0b' }} }}
    }}
  }}
}});

const idxCtx = document.getElementById('idxChart');
const idxChart = new Chart(idxCtx, {{
  type: 'line',
  data: {{
    labels: DATA.weeks[0].idx.map(p => p.d.slice(5)),
    datasets: [{{
      data: DATA.weeks[0].idx.map(p => p.c),
      borderColor: '#534AB7',
      backgroundColor: 'rgba(83,74,183,0.1)',
      fill: true,
      tension: 0.2,
      pointRadius: 3,
      pointBackgroundColor: '#534AB7',
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    animation: {{ duration: 350 }},
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ ticks: {{ color: '#898781' }} }},
      y: {{ ticks: {{ color: '#898781' }} }}
    }}
  }}
}});

const slider = document.getElementById('weekSlider');
const label = document.getElementById('weekLabel');
const playBtn = document.getElementById('playBtn');
const speedSel = document.getElementById('speedSel');
const sumInEl = document.getElementById('sumIn');
const sumOutEl = document.getElementById('sumOut');
const sumNetEl = document.getElementById('sumNet');
let playing = false;
let timer = null;

function renderWeek(i) {{
  const wk = DATA.weeks[i];
  chart.data.datasets[0].data = wk.v;
  chart.data.datasets[0].backgroundColor = wk.v.map(v => v >= 0 ? '#378ADD' : '#D85A30');
  chart.update();
  label.textContent = wk.s + ' ~ ' + wk.e + '（第 ' + (i + 1) + ' / ' + DATA.weeks.length + ' 週）';
  slider.value = i;

  const sumIn = wk.v.filter(v => v > 0).reduce((a, b) => a + b, 0);
  const sumOut = wk.v.filter(v => v < 0).reduce((a, b) => a + b, 0);
  sumInEl.textContent = '+' + sumIn.toFixed(1);
  sumOutEl.textContent = sumOut.toFixed(1);
  sumNetEl.textContent = (sumIn + sumOut >= 0 ? '+' : '') + (sumIn + sumOut).toFixed(1);

  idxChart.data.labels = wk.idx.map(p => p.d.slice(5));
  idxChart.data.datasets[0].data = wk.idx.map(p => p.c);
  idxChart.update();
}}

function stepForward() {{
  let i = parseInt(slider.value, 10) + 1;
  if (i >= DATA.weeks.length) {{ i = 0; }}
  renderWeek(i);
}}

function setPlaying(v) {{
  playing = v;
  playBtn.textContent = playing ? '暫停' : '播放';
  if (timer) {{ clearInterval(timer); timer = null; }}
  if (playing) {{ timer = setInterval(stepForward, parseInt(speedSel.value, 10)); }}
}}

playBtn.addEventListener('click', () => setPlaying(!playing));
speedSel.addEventListener('change', () => {{ if (playing) setPlaying(true); }});
slider.addEventListener('input', () => {{ setPlaying(false); renderWeek(parseInt(slider.value, 10)); }});

renderWeek(0);
</script>
</body></html>
"""


def export(db_path: Path, out_path: Path, top_n: int) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        activity = conn.execute(
            "SELECT industry_name, SUM(ABS(total_net)) as act FROM sector_flow_weekly "
            "GROUP BY industry_name ORDER BY act DESC LIMIT ?",
            (top_n,),
        ).fetchall()
        sectors = [r[0] for r in activity]
        sector_idx = {s: i for i, s in enumerate(sectors)}

        placeholders = ",".join("?" * len(sectors))
        rows = conn.execute(
            f"SELECT industry_name, week_index, week_start, week_end, total_net "
            f"FROM sector_flow_weekly WHERE industry_name IN ({placeholders}) ORDER BY week_index",
            sectors,
        ).fetchall()

        weeks: dict[int, dict] = {}
        for industry_name, week_index, week_start, week_end, total_net in rows:
            weeks.setdefault(week_index, {"s": week_start, "e": week_end, "v": [0.0] * len(sectors)})
            weeks[week_index]["v"][sector_idx[industry_name]] = round(total_net / 100_000_000, 1)

        # 補上每週對應的 TAIEX 每日收盤（該週日期區間內實際有資料的交易日，通常 5 筆，
        # 若 taiex_daily 剛好缺某天就少一筆，不臆測補值）。
        taiex_rows = conn.execute(
            "SELECT date, close FROM taiex_daily ORDER BY date"
        ).fetchall()
        for week_index, wk in weeks.items():
            wk["idx"] = [
                {"d": d, "c": round(c, 0)}
                for d, c in taiex_rows
                if wk["s"] <= d <= wk["e"]
            ]

        out_weeks = [weeks[i] for i in sorted(weeks.keys())]
        data = {"sectors": sectors, "weeks": out_weeks}
        data_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

        # 固定 x 軸範圍（不隨每週資料自動縮放），讓 0 軸位置在整個動畫播放過程中
        # 保持在畫面同一個像素位置，不會因為某一週數值特別大/小而左右移動。
        # 用全部 top-N 板塊、全部週次的實際極值取整數 padding，兩側對稱留一點餘裕。
        all_values = [v for wk in out_weeks for v in wk["v"]]
        raw_min, raw_max = min(all_values), max(all_values)
        x_min = int(raw_min) - 1
        x_max = int(raw_max) + 1

        # 注意：TAIEX 折線圖的 y 軸**刻意不固定**，讓 Chart.js 依當週實際指數區間自動
        # 縮放——這裡跟長條圖的 x 軸固定邏輯相反，因為長條圖的 0 軸是「正負方向的錨點」
        # 需要固定；但指數走勢圖是絕對價位，3 年間指數從約 17,200 漲到約 47,700，若把
        # y 軸固定成全域範圍，單週幾百點的正常波動會被壓成一條幾乎看不出起伏的平線，
        # 反而看不出「當週走勢」，自動縮放才能看清楚每週實際的漲跌形狀。

        html = _TEMPLATE.format(
            data_json=data_json, max_week=len(out_weeks) - 1, x_min=x_min, x_max=x_max, top_n=len(sectors),
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")

        return {"sectors": len(sectors), "weeks": len(out_weeks), "out_path": str(out_path)}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    parser.add_argument("--top-n", type=int, default=20)
    args = parser.parse_args()

    summary = export(args.db_path, args.out, args.top_n)
    print(f"匯出完成：{summary['sectors']} 個板塊、{summary['weeks']} 週 -> {summary['out_path']}")


if __name__ == "__main__":
    main()
