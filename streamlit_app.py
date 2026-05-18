from __future__ import annotations

import html
import json
import re
import ssl
import urllib.request
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components


ROOT      = Path(__file__).resolve().parent
HTML_PATH = ROOT / "docs" / "index.html"
SSL_CONTEXT = ssl._create_unverified_context()


# ── Helpers ────────────────────────────────────────────────────────────────────
def extract_symbols(base_html: str) -> list[dict[str, str]]:
    """掃描靜態 HTML 裡的 data-code 屬性，取出代號與市場。"""
    pattern = re.compile(
        r'<tr data-code="(?P<code>\d{4})"[\s\S]*?'
        r'class="mkt-badge">(?P<market>.*?)</span>',
        re.S,
    )
    seen: set[str] = set()
    symbols: list[dict[str, str]] = []
    for m in pattern.finditer(base_html):
        code = m.group("code")
        if code in seen:
            continue
        seen.add(code)
        market_text = re.sub(r"<.*?>", "", html.unescape(m.group("market")))
        exchange = "otc" if "櫃" in market_text else "tse"
        symbols.append({"code": code, "exchange": exchange})
    return symbols


def estimate_page_height(base_html: str) -> int:
    num_rows   = base_html.count('data-code="')
    num_groups = base_html.count('class="group-card"')
    return max(4000, 1000 + num_groups * 68 + num_rows * 38)


def inject_live_script(base_html: str, symbols: list[dict[str, str]]) -> str:
    """
    架構改為：瀏覽器 JS 直接呼叫 TWSE MIS API 取得即時報價。
    - 伺服器端 (Python) 不再抓報價，避免 403。
    - TWSE MIS 同時支援 tse_代號.tw 和 otc_代號.tw，一個 endpoint 搞定。
    - 分批呼叫（每批 ≤ 50 檔），避免 URL 過長。
    - 盤中更新：每 15 秒自動刷新一次。
    - 非交易時間（z="-"）fallback 到靜態 HTML 的 data-change。
    """
    symbols_json = json.dumps(symbols, ensure_ascii=False)

    script = f"""
<script>
(function () {{
  "use strict";

  /* ── 股票清單（Python 在 build 時寫入） ──────────────────────────── */
  const SYMBOLS = {symbols_json};

  /* ── 格式化工具 ─────────────────────────────────────────────────── */
  const fmtPrice = v =>
    (v == null || !isFinite(+v)) ? "--"
    : (+v).toLocaleString("en-US", {{minimumFractionDigits:2, maximumFractionDigits:2}});

  const fmtInt = v =>
    (v == null || !isFinite(+v)) ? "--"
    : Math.round(+v).toLocaleString("en-US");

  const fmtPct = v => {{
    if (v == null || !isFinite(+v)) return "--";
    return (+v > 0 ? "+" : "") + (+v).toFixed(2) + "%";
  }};

  const trend = v =>
    (v == null || !isFinite(+v)) ? "na"
    : +v > 0 ? "up" : +v < 0 ? "down" : "flat";

  const heat = v => {{
    if (v == null || !isFinite(+v)) return "rgba(12,22,40,0.25)";
    const s = Math.min(Math.abs(+v) / 6, 1);
    const a = (0.14 + s * 0.46).toFixed(3);
    return +v > 0 ? `rgba(255,45,84,${{a}})` : `rgba(0,210,110,${{a}})`;
  }};

  /* ── 分批抓 TWSE MIS API（瀏覽器直呼，不過 403）────────────────── */
  const BATCH = 50;
  const MIS   = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp";

  async function fetchBatch(items) {{
    const ex_ch = items.map(s => `${{s.exchange}}_${{s.code}}.tw`).join("|");
    const url   = `${{MIS}}?ex_ch=${{encodeURIComponent(ex_ch)}}&json=1&delay=0&_=${{Date.now()}}`;
    const resp  = await fetch(url, {{
      headers: {{
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://mis.twse.com.tw/stock/index.jsp",
      }},
      credentials: "omit",
    }});
    if (!resp.ok) throw new Error(`HTTP ${{resp.status}}`);
    return resp.json();
  }}

  async function fetchAllQuotes() {{
    const allQuotes = {{}};
    let latestTime = "--:--:--", latestDate = "--";

    for (let i = 0; i < SYMBOLS.length; i += BATCH) {{
      const batch = SYMBOLS.slice(i, i + BATCH);
      try {{
        const data = await fetchBatch(batch);
        const qt   = data.queryTime || {{}};
        if (qt.sysTime) latestTime = qt.sysTime;
        if (qt.sysDate) {{
          const d = qt.sysDate;
          latestDate = d.length === 8
            ? `${{d.slice(0,4)}}-${{d.slice(4,6)}}-${{d.slice(6,8)}}`
            : d;
        }}
        for (const item of (data.msgArray || [])) {{
          const code = (item.c || "").trim();
          if (!code) continue;

          /* z = 即時成交價；盤前盤後為 "-"
             y = 昨收；v = 累積成交量（張） */
          const rawZ  = (item.z || "").trim();
          const rawY  = (item.y || "").trim();
          const price = (rawZ && rawZ !== "-") ? parseFloat(rawZ) : parseFloat(rawY);
          const prev  = parseFloat(rawY);

          /* 只有真正有成交（z 有值）才算漲跌幅，否則 null（不污染平均） */
          let changePct = null;
          if (rawZ && rawZ !== "-" && isFinite(price) && isFinite(prev) && prev !== 0) {{
            changePct = (price / prev - 1) * 100;
          }}

          const volume = parseFloat(item.v);   // 累積成交量（張）

          allQuotes[code] = {{
            price:      isFinite(price)  ? price  : null,
            changePct:  changePct,
            volume:     isFinite(volume) ? volume : null,
          }};
        }}
      }} catch (e) {{
        console.warn("fetchBatch error", e);
      }}
    }}
    return {{ quotes: allQuotes, latestTime, latestDate }};
  }}

  /* ── 把報價更新進 DOM ────────────────────────────────────────────── */
  function applyQuotes({{ quotes, latestTime, latestDate }}) {{
    const groupStats = new Map();
    let totalChange = 0, totalCount = 0, totalVolume = 0;

    document.querySelectorAll("tr[data-code]").forEach(row => {{
      const code  = row.dataset.code;
      const q     = quotes[code];

      let price     = q ? q.price     : null;
      let changePct = q ? q.changePct : null;
      let volume    = q ? q.volume    : null;

      /* fallback：盤前/盤後 changePct=null 時用靜態 data-change（昨收漲跌） */
      if (changePct == null) {{
        const raw = parseFloat(row.dataset.change);
        if (isFinite(raw)) changePct = raw;
      }}

      /* 更新 dataset（給表格排序用） */
      if (price     != null && isFinite(price))     row.dataset.price  = price;
      if (changePct != null && isFinite(changePct)) row.dataset.change = changePct;
      if (volume    != null && isFinite(volume))    row.dataset.volume = volume;

      /* 更新 TD 內容 */
      const nums = row.querySelectorAll("td.num");
      if (price     != null && nums[0]) nums[0].textContent = fmtPrice(price);
      if (changePct != null && nums[1]) {{
        nums[1].textContent = fmtPct(changePct);
        nums[1].className   = `num ${{trend(changePct)}}`;
      }}
      if (volume != null && nums[2]) nums[2].textContent = fmtInt(volume);

      /* 累計 group 統計 */
      const group = row.dataset.group || "";
      if (!groupStats.has(group)) groupStats.set(group, {{sum:0, count:0, vol:0}});
      const stat = groupStats.get(group);
      if (changePct != null && isFinite(changePct)) {{
        stat.sum += changePct; stat.count++;
        totalChange += changePct; totalCount++;
      }}
      if (volume != null && isFinite(volume)) {{
        stat.vol += volume; totalVolume += volume;
      }}
    }});

    /* KPI 列 */
    const avg = totalCount ? totalChange / totalCount : null;
    const kpi = document.querySelectorAll(".kpi .value");
    if (kpi[2]) {{ kpi[2].textContent = fmtPct(avg); kpi[2].className = `value ${{trend(avg)}}`; }}
    if (kpi[3]) kpi[3].textContent = fmtInt(totalVolume) + "張";
    if (kpi[4]) kpi[4].textContent = latestTime;

    /* Toolbar meta */
    const meta = document.querySelector(".toolbar-meta");
    if (meta) meta.innerHTML =
      `共 <b>${{SYMBOLS.length}}</b> 檔 | 即時 <b>${{latestDate}} ${{latestTime}}</b>`;

    /* Group chips */
    document.querySelectorAll(".group-card").forEach(card => {{
      const stat = groupStats.get(card.dataset.group);
      if (!stat || !stat.count) return;
      const a    = stat.sum / stat.count;
      const chip = card.querySelector(".group-chip");
      if (chip) {{ chip.textContent = fmtPct(a); chip.className = `group-chip ${{trend(a)}}`; }}
    }});

    /* Stage 熱力格 */
    document.querySelectorAll(".stage-heat-cell").forEach(cell => {{
      const stat = groupStats.get(cell.dataset.filter);
      if (!stat || !stat.count) return;
      const a = stat.sum / stat.count;
      cell.classList.remove("up", "down", "flat", "na");
      cell.classList.add(trend(a));
      cell.style.setProperty("--heat", heat(a));
      const ce = cell.querySelector(".stage-heat-change");
      const ve = cell.querySelector(".stage-heat-volume");
      if (ce) ce.textContent = fmtPct(a);
      if (ve) ve.textContent = fmtInt(stat.vol) + " 張";
    }});

    /* 成交量排行 */
    const board = document.getElementById("vol-leaderboard");
    if (board) {{
      const ranked = Array.from(document.querySelectorAll("tr[data-code]"))
        .map(row => {{
          const q   = quotes[row.dataset.code];
          const vol = q && q.volume != null ? +q.volume : null;
          if (!vol || !isFinite(vol)) return null;
          let chg = (q && q.changePct != null && isFinite(q.changePct))
            ? q.changePct : parseFloat(row.dataset.change);
          if (!isFinite(chg)) chg = null;
          return {{
            code:  row.dataset.code,
            name:  row.dataset.name || row.dataset.code,
            vol, chg,
            price: q && q.price != null ? +q.price : null,
          }};
        }})
        .filter(Boolean)
        .sort((a, b) => b.vol - a.vol)
        .slice(0, 12);

      board.innerHTML = ranked.map(s => `
        <div class="vol-card">
          <div class="vol-top">
            <span class="vol-code">${{s.code}}</span>
            <span class="vol-chg ${{trend(s.chg)}}">${{fmtPct(s.chg)}}</span>
          </div>
          <div class="vol-name">${{s.name}}</div>
          <div class="vol-vol">${{fmtInt(s.vol)}} 張</div>
        </div>`).join("");
    }}
  }}

  /* ── 主流程：載入完立即抓，之後每 15 秒更新 ──────────────────── */
  async function refresh() {{
    try {{
      const result = await fetchAllQuotes();
      applyQuotes(result);
    }} catch(e) {{
      console.error("refresh error", e);
    }}
  }}

  // 頁面載入完後立即跑一次，之後每 15 秒
  refresh();
  setInterval(refresh, 15000);
}})();
</script>
"""

    vol_html = (
        "<style>"
        ".vol-strip{padding:0 18px 12px}"
        ".vol-label{font-size:10px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:#22d3ee;margin-bottom:9px}"
        "#vol-leaderboard{display:flex;gap:8px;overflow-x:auto;padding-bottom:4px;scrollbar-width:thin;scrollbar-color:#3d5470 transparent}"
        "#vol-leaderboard::-webkit-scrollbar{height:4px}"
        "#vol-leaderboard::-webkit-scrollbar-thumb{background:#3d5470;border-radius:2px}"
        ".vol-card{flex:0 0 116px;background:#070d1a;border:1px solid rgba(255,255,255,0.07);border-top:2px solid #22d3ee;border-radius:10px;padding:10px 12px 9px;min-width:0}"
        ".vol-top{display:flex;justify-content:space-between;align-items:baseline;gap:4px}"
        ".vol-code{font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:600;color:#22d3ee}"
        ".vol-chg{font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600}"
        ".vol-name{font-size:12px;font-weight:600;margin:5px 0 3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
        ".vol-vol{font-size:11px;color:#7a9bbb}"
        "</style>"
        '<div class="vol-strip">'
        '<div class="vol-label">🔥 成交量排行</div>'
        '<div id="vol-leaderboard"><div style="color:#3d5470;font-size:12px;padding:8px">載入中…</div></div>'
        "</div>"
    )

    out = base_html.replace('<main id="groupList">', vol_html + '<main id="groupList">', 1)
    out = out.replace("</body>", script + "</body>", 1)
    return out


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    st.set_page_config(
        page_title="台灣半導體 × AI 產業鏈",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    st.markdown(
        """
        <style>
          [data-testid="stHeader"]           { display: none !important; }
          [data-testid="stToolbar"]          { display: none !important; }
          [data-testid="stDecoration"]       { display: none !important; }
          [data-testid="stSidebar"]          { display: none !important; }
          [data-testid="stAppViewContainer"] { padding: 0 !important; }
          [data-testid="stVerticalBlock"]    { gap: 0 !important; padding: 0 !important; }
          [data-testid="element-container"]  { padding: 0 !important; margin: 0 !important; }
          #MainMenu                          { display: none !important; }
          footer                             { display: none !important; }
          .block-container                   { padding: 0 !important; max-width: 100% !important; }
          .stApp > header                    { display: none !important; }
          .stApp                             { overflow: hidden; }
          iframe                             { display: block !important; border: none !important; margin: 0 !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    if not HTML_PATH.exists():
        st.error(
            f"❌ 找不到 `{HTML_PATH}`\n\n"
            "請確認 GitHub Actions `Update Static Heatmap Data` 已成功執行，"
            "且 `docs/index.html` 已被 commit 進 repo。"
        )
        st.stop()

    html_content = HTML_PATH.read_text(encoding="utf-8")
    symbols = extract_symbols(html_content)

    if not symbols:
        st.error("HTML 裡沒有找到任何 data-code，靜態檔案可能有問題，請重新執行 build 腳本。")
        st.stop()

    # 不再由 Python 抓報價，改由瀏覽器 JS 直接打 TWSE MIS API
    html_content = inject_live_script(html_content, symbols)
    page_height  = estimate_page_height(html_content)
    components.html(html_content, height=page_height, scrolling=False)


if __name__ == "__main__":
    main()
