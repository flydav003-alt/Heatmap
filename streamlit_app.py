"""
streamlit_app.py — 台灣半導體 × AI 供應鏈熱力圖
──────────────────────────────────────────────────
完全 native Streamlit 渲染，無任何 iframe / components.html。
資料來源：
  · docs/representative_chain_data.json（build 腳本每 15 分鐘更新）
  · TWSE MIS 即時報價 API（盤中每 20 秒快取一次）
"""
from __future__ import annotations

import json
import re
import ssl
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import streamlit as st

# ── 路徑 ────────────────────────────────────────────────────────────────────
ROOT      = Path(__file__).resolve().parent
JSON_PATH = ROOT / "docs" / "representative_chain_data.json"

# ── TWSE MIS ────────────────────────────────────────────────────────────────
TWSE_MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
SSL_CTX      = ssl._create_unverified_context()

# 主題對應顏色（accent / 左邊框）
THEME_COLORS: dict[str, str] = {
    "ASIC":   "#8b5cf6",
    "CoWoS":  "#ec4899",
    "HBM":    "#7c3aed",
    "CPO":    "#0ea5e9",
    "BBU":    "#f97316",
    "伺服器":  "#f43f5e",
    "散熱":   "#38bdf8",
    "載板PCB": "#06b6d4",
}

# ── 工具函式 ─────────────────────────────────────────────────────────────────
def parse_float(v: Any) -> float | None:
    if v in (None, "", "-", "--", "----"):
        return None
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return None


def fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"+{v:.2f}%" if v > 0 else f"{v:.2f}%"


def fmt_price(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.2f}"


def fmt_vol(v: float | None) -> str:
    """成交量（千股 → 張）"""
    if v is None:
        return "—"
    return f"{v / 1000:,.0f}"


def trend_color(v: float | None) -> str:
    if v is None:
        return "#6b85a8"
    return "#ef4444" if v > 0 else ("#22c55e" if v < 0 else "#f59e0b")


def heat_bg(v: float | None) -> str:
    """背景顏色：紅漲綠跌（台股邏輯）"""
    if v is None:
        return "rgba(100,116,139,0.18)"
    mag   = min(abs(v) / 6.0, 1.0)
    alpha = 0.12 + mag * 0.45
    if v > 0:
        return f"rgba(239,68,68,{alpha:.3f})"
    if v < 0:
        return f"rgba(34,197,94,{alpha:.3f})"
    return "rgba(245,158,11,0.22)"


# ── 即時報價 ─────────────────────────────────────────────────────────────────
@st.cache_data(ttl=20, show_spinner=False)
def fetch_live_quotes(codes: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """
    同時送出 tse_XXXX.tw 與 otc_XXXX.tw；
    API 只回傳有效的那個 exchange，避免需要預先知道上市/上櫃。
    """
    ex_parts = []
    for c in codes:
        ex_parts.append(f"tse_{c}.tw")
        ex_parts.append(f"otc_{c}.tw")

    ex_ch  = "|".join(ex_parts)
    params = urllib.parse.urlencode(
        {"ex_ch": ex_ch, "json": "1", "delay": "0", "_": str(int(time.time() * 1000))},
        safe="|_.",
    )
    url = f"{TWSE_MIS_URL}?{params}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,*/*",
            "Referer": "https://mis.twse.com.tw/stock/index.jsp",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20, context=SSL_CTX) as resp:
            payload = json.loads(resp.read().decode("utf-8-sig"))
    except Exception:
        return {}

    quotes: dict[str, dict[str, Any]] = {}
    for item in payload.get("msgArray", []):
        code  = str(item.get("c", "")).strip()
        price = parse_float(item.get("z"))
        prev  = parse_float(item.get("y"))
        if price is None:
            price = parse_float(item.get("pz")) or prev
        change_pct = ((price / prev - 1) * 100) if price and prev else None
        if code:
            quotes[code] = {
                "price":      price,
                "change_pct": change_pct,
                "volume":     parse_float(item.get("v")),
                "time":       item.get("t", ""),
                "date":       item.get("d", ""),
            }
    return quotes


# ── UI 元件（全用 st.markdown unsafe html，無 iframe）───────────────────────

def _css() -> str:
    return """
<style>
/* ── Reset & Base ─────────────────────────────────────────── */
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;700;900&display=swap');

.stApp { background: #060d19 !important; }
.block-container {
  padding: 1.2rem 1.6rem 2rem !important;
  max-width: 100% !important;
}
[data-testid="stHeader"]   { background: transparent !important; }
[data-testid="stSidebar"]  { display: none !important; }
[data-testid="stToolbar"]  { display: none !important; }
footer                     { display: none !important; }

/* ── Typography ───────────────────────────────────────────── */
* { font-family: "Noto Sans TC", "Segoe UI", system-ui, sans-serif !important; }
h1, h2, h3 { color: #e8f1ff !important; }

/* ── Metric Cards ─────────────────────────────────────────── */
[data-testid="stMetric"] {
  background: rgba(10,21,38,0.95) !important;
  border: 1px solid #162c47 !important;
  border-radius: 16px !important;
  padding: 14px 18px !important;
}
[data-testid="stMetricLabel"] { color: #6b85a8 !important; font-size: 11px !important; letter-spacing: .08em; }
[data-testid="stMetricValue"] { color: #e8f1ff !important; font-size: 22px !important; font-weight: 900 !important; }

/* ── Columns ──────────────────────────────────────────────── */
[data-testid="column"] { padding: 4px !important; }

/* ── Expander ─────────────────────────────────────────────── */
[data-testid="stExpander"] > details > summary {
  background: rgba(10,21,38,0.95) !important;
  border: 1px solid #162c47 !important;
  border-radius: 12px !important;
  color: #e8f1ff !important;
  font-weight: 800 !important;
  padding: 12px 18px !important;
}
[data-testid="stExpander"] > details > summary:hover {
  border-color: #38d1ff !important;
}
[data-testid="stExpander"] > details[open] > summary {
  border-radius: 12px 12px 0 0 !important;
}
[data-testid="stExpander"] .stExpanderDetails {
  background: rgba(8,17,31,0.98) !important;
  border: 1px solid #162c47 !important;
  border-top: none !important;
  border-radius: 0 0 12px 12px !important;
  padding: 0 !important;
}

/* ── Spinner ──────────────────────────────────────────────── */
[data-testid="stSpinner"] { color: #38d1ff !important; }

/* ── Markdown / general ───────────────────────────────────── */
.stMarkdown { color: #e8f1ff; }
div[data-testid="stMarkdownContainer"] p { margin: 0; }

/* ── Search input ─────────────────────────────────────────── */
.hm-search { width: 100%; }
.hm-search input {
  background: rgba(10,21,38,0.95);
  border: 1px solid #1a3558;
  border-radius: 12px;
  color: #e8f1ff;
  font-size: 14px;
  padding: 11px 16px;
  outline: none;
  width: 100%;
  transition: border-color .15s;
}
.hm-search input:focus { border-color: #38d1ff; }

/* ── Heat Cell ────────────────────────────────────────────── */
.heat-cell {
  border-radius: 14px;
  padding: 14px 14px 12px;
  border: 1px solid rgba(255,255,255,0.07);
  border-left-width: 4px;
  transition: transform .12s, box-shadow .12s;
  cursor: default;
}
.heat-cell:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 28px rgba(0,0,0,.45);
}

/* ── Stock Table ──────────────────────────────────────────── */
.hm-table {
  width: 100%;
  border-collapse: collapse;
}
.hm-table th {
  background: rgba(6,13,25,.92);
  color: #6b85a8;
  text-align: left;
  padding: 10px 12px;
  font-size: 11px;
  letter-spacing: .06em;
  text-transform: uppercase;
  border-bottom: 1px solid #162c47;
  white-space: nowrap;
}
.hm-table td {
  padding: 11px 12px;
  border-bottom: 1px solid rgba(22,44,71,.5);
  font-size: 13px;
  vertical-align: middle;
  color: #e8f1ff;
}
.hm-table tbody tr:last-child td { border-bottom: none; }
.hm-table tbody tr:hover { background: rgba(14,31,49,.4); }
.hm-table .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.hm-up   { color: #ef4444; }
.hm-down { color: #22c55e; }
.hm-flat { color: #f59e0b; }
.hm-na   { color: #5a7090; }

/* ── Divider ──────────────────────────────────────────────── */
.hm-divider {
  height: 1px;
  background: #162c47;
  margin: 16px 0;
}
</style>
"""


def _header_html(total_stocks: int, total_themes: int,
                 avg_change: float | None, is_live: bool,
                 live_time: str, latest_date: str) -> str:
    tc   = trend_color(avg_change)
    pct  = fmt_pct(avg_change)
    status_dot = "#22c55e" if is_live else "#f59e0b"
    status_txt = f"即時 {live_time}" if is_live else f"靜態快照 {latest_date}"

    return f"""
<div style="padding:16px 0 4px;">
  <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
    <div>
      <h1 style="
        margin:0; font-size:28px; font-weight:900; letter-spacing:-.5px;
        background:linear-gradient(130deg,#e8f1ff 0%,#38d1ff 100%);
        -webkit-background-clip:text; -webkit-text-fill-color:transparent;
        background-clip:text;
      ">台灣半導體 × AI 供應鏈熱力圖</h1>
      <div style="margin-top:5px;color:#6b85a8;font-size:11px;letter-spacing:.12em;text-transform:uppercase;">
        Taiwan Semiconductor &amp; AI Supply Chain · 台股紅漲綠跌邏輯
      </div>
    </div>
  </div>

  <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:16px;">
    <div style="background:rgba(10,21,38,.95);border:1px solid #162c47;border-radius:16px;padding:14px 20px;min-width:110px;">
      <div style="color:#6b85a8;font-size:10px;letter-spacing:.08em;text-transform:uppercase;">收錄代表股</div>
      <div style="color:#e8f1ff;font-size:22px;font-weight:900;margin-top:6px;">{total_stocks} 檔</div>
    </div>
    <div style="background:rgba(10,21,38,.95);border:1px solid #162c47;border-radius:16px;padding:14px 20px;min-width:110px;">
      <div style="color:#6b85a8;font-size:10px;letter-spacing:.08em;text-transform:uppercase;">主題數</div>
      <div style="color:#e8f1ff;font-size:22px;font-weight:900;margin-top:6px;">{total_themes} 個</div>
    </div>
    <div style="background:rgba(10,21,38,.95);border:1px solid #162c47;border-radius:16px;padding:14px 20px;min-width:110px;">
      <div style="color:#6b85a8;font-size:10px;letter-spacing:.08em;text-transform:uppercase;">代表股均漲跌</div>
      <div style="color:{tc};font-size:22px;font-weight:900;margin-top:6px;">{pct}</div>
    </div>
    <div style="background:rgba(10,21,38,.95);border:1px solid #162c47;border-radius:16px;padding:14px 20px;min-width:180px;">
      <div style="color:#6b85a8;font-size:10px;letter-spacing:.08em;text-transform:uppercase;">資料狀態</div>
      <div style="display:flex;align-items:center;gap:6px;margin-top:7px;">
        <span style="width:8px;height:8px;border-radius:50%;background:{status_dot};display:inline-block;flex-shrink:0;"></span>
        <span style="color:#e8f1ff;font-size:13px;font-weight:700;">{status_txt}</span>
      </div>
    </div>
  </div>
</div>
"""


def _section_label(text: str) -> str:
    return f"""
<div style="display:flex;align-items:center;gap:8px;margin:20px 0 12px;">
  <span style="display:inline-block;width:3px;height:16px;background:#38d1ff;border-radius:2px;flex-shrink:0;"></span>
  <span style="color:#38d1ff;font-size:11px;font-weight:900;letter-spacing:.15em;text-transform:uppercase;">{text}</span>
</div>
"""


def _heat_cell_html(theme: str, avg: float | None,
                    vol_lots: float | None, count: int) -> str:
    accent = THEME_COLORS.get(theme, "#38d1ff")
    bg     = heat_bg(avg)
    tc     = trend_color(avg)
    pct    = fmt_pct(avg)
    vol_s  = f"{vol_lots/1000:,.0f} 張" if vol_lots else "—"

    return f"""
<div class="heat-cell" style="background:{bg};border-left-color:{accent};">
  <div style="font-size:14px;font-weight:900;color:#e8f1ff;line-height:1.2;">{theme}</div>
  <div style="font-size:24px;font-weight:900;color:{tc};margin-top:8px;line-height:1;">{pct}</div>
  <div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px;">
    <span style="font-size:11px;color:rgba(232,241,255,.65);">{vol_s}</span>
    <span style="font-size:11px;color:rgba(232,241,255,.45);">{count} 檔</span>
  </div>
</div>
"""


def _stock_table_html(stocks: list[dict], live: dict[str, dict]) -> str:
    rows = ""
    for s in stocks:
        code = s["code"]
        name = s["name"]
        q    = live.get(code, {})

        price      = q.get("price")      or s.get("price")
        change_pct = q.get("change_pct") if q.get("change_pct") is not None else s.get("change_pct")
        volume     = q.get("volume")     or s.get("volume_lots")

        tc2     = trend_color(change_pct)
        cls     = "hm-up" if (change_pct or 0) > 0 else ("hm-down" if (change_pct or 0) < 0 else "hm-na")
        pct_str = fmt_pct(change_pct)
        pri_str = fmt_price(price)
        vol_str = (f"{volume/1000:,.0f}" if volume else "—") if volume and code not in live else \
                  (f"{volume:,.0f}" if volume else "—")

        # 如果是從 live 拿到的 volume，單位是千股；JSON 裡是張（已轉換）
        if code in live and live[code].get("volume"):
            vol_str = fmt_vol(live[code]["volume"])
        elif s.get("volume_lots"):
            vol_str = f"{s['volume_lots']:,.0f}"

        live_dot = '<span style="width:5px;height:5px;border-radius:50%;background:#22c55e;display:inline-block;margin-left:4px;vertical-align:middle;"></span>' if code in live else ""

        rows += f"""
<tr>
  <td>
    <span style="color:#38d1ff;font-size:15px;font-weight:900;">{code}</span>
    {live_dot}
  </td>
  <td><span style="font-weight:800;">{name}</span></td>
  <td class="num">{pri_str}</td>
  <td class="num {cls}" style="color:{tc2};font-weight:900;">{pct_str}</td>
  <td class="num" style="color:#6b85a8;">{vol_str}</td>
</tr>
"""

    return f"""
<div style="background:rgba(6,13,25,.6);border-radius:12px;overflow:hidden;border:1px solid #162c47;">
  <table class="hm-table">
    <thead>
      <tr>
        <th>代號</th>
        <th>公司名稱</th>
        <th style="text-align:right;">股價</th>
        <th style="text-align:right;">漲跌幅</th>
        <th style="text-align:right;">成交量(張)</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
</div>
"""


def _footer_html(latest_date: str, updated_at: str, is_live: bool) -> str:
    src = "TWSE MIS 即時" if is_live else "靜態快照"
    return f"""
<div style="margin-top:24px;padding-top:20px;border-top:1px solid #162c47;color:#6b85a8;font-size:11px;line-height:1.9;">
  <b style="color:#e8f1ff;">⚠ 注意：</b> 紅色 = 上漲、綠色 = 下跌（台股邏輯，與美股相反）。
  本頁僅顯示各主題代表股。完整半導體產業鏈（200+ 檔）請至 GitHub Pages 靜態版。<br>
  <b style="color:#e8f1ff;">資料來源：</b> {src} ·
  靜態基準：{latest_date} ·
  JSON 更新：{updated_at} ·
  即時快取：20 秒
</div>
"""


# ── 主程式 ───────────────────────────────────────────────────────────────────
def main() -> None:
    st.set_page_config(
        page_title="台灣半導體 AI 供應鏈熱力圖",
        page_icon="🔥",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    # 注入 CSS（不含任何 iframe）
    st.markdown(_css(), unsafe_allow_html=True)

    # ── 載入 JSON ──────────────────────────────────────────────────────────
    if not JSON_PATH.exists():
        st.error(
            "找不到 `docs/representative_chain_data.json`。\n\n"
            "請先在本機執行：`python docs/build_semiconductor_ai_chain.py`\n"
            "或等 GitHub Actions 自動更新後重啟。"
        )
        st.stop()

    with open(JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    themes      = data.get("themes", {})
    updated_at  = data.get("updated_at", "—")
    latest_date = data.get("latest_price_date", "—")

    # ── 收集所有代表股代號 ──────────────────────────────────────────────
    all_codes: list[str] = []
    for td in themes.values():
        for s in td.get("stocks", []):
            if s["code"] not in all_codes:
                all_codes.append(s["code"])

    # ── 抓即時報價 ──────────────────────────────────────────────────────
    with st.spinner(f"正在抓取 {len(all_codes)} 檔即時報價…"):
        live = fetch_live_quotes(tuple(all_codes))

    is_live  = bool(live)
    live_time = next(
        (q["time"] for q in live.values() if q.get("time")), ""
    )

    # ── 統計全域平均漲跌 ────────────────────────────────────────────────
    all_changes: list[float] = []
    for td in themes.values():
        for s in td.get("stocks", []):
            cp = live.get(s["code"], {}).get("change_pct") \
                 if s["code"] in live else s.get("change_pct")
            if cp is not None:
                all_changes.append(cp)
    avg_change = sum(all_changes) / len(all_changes) if all_changes else None

    # ── Header ─────────────────────────────────────────────────────────
    st.markdown(
        _header_html(len(all_codes), len(themes),
                     avg_change, is_live, live_time, latest_date),
        unsafe_allow_html=True,
    )

    # ── 非交易時間提示 ──────────────────────────────────────────────────
    if not is_live:
        st.warning(
            "TWSE MIS 目前無即時資料（非交易時間或 API 暫時不可用）。"
            "顯示的是靜態快照數據。",
            icon="⏸",
        )

    # ── Heatmap ────────────────────────────────────────────────────────
    st.markdown(_section_label("主題熱力圖 · Theme Heatmap"), unsafe_allow_html=True)

    theme_keys = list(themes.keys())
    COLS = 4
    for i in range(0, len(theme_keys), COLS):
        batch = theme_keys[i : i + COLS]
        cols  = st.columns(len(batch))
        for col, key in zip(cols, batch):
            td     = themes[key]
            stocks = td.get("stocks", [])
            changes = [
                live[s["code"]]["change_pct"]
                if s["code"] in live and live[s["code"]].get("change_pct") is not None
                else s.get("change_pct")
                for s in stocks
            ]
            changes = [c for c in changes if c is not None]
            avg_t   = sum(changes) / len(changes) if changes else None
            vol_sum = sum(
                (live[s["code"]].get("volume") or 0) / 1000
                if s["code"] in live
                else (s.get("volume_lots") or 0)
                for s in stocks
            )
            with col:
                st.markdown(
                    _heat_cell_html(key, avg_t, vol_sum * 1000, len(stocks)),
                    unsafe_allow_html=True,
                )

    # ── 各主題代表股明細 ────────────────────────────────────────────────
    st.markdown(_section_label("各主題代表股明細"), unsafe_allow_html=True)

    # 搜尋框（session_state）
    search_key = "hm_search"
    if search_key not in st.session_state:
        st.session_state[search_key] = ""

    search = st.text_input(
        label="",
        placeholder="🔍  搜尋股票代號或公司名稱…",
        key=search_key,
        label_visibility="collapsed",
    )
    term = search.strip().lower()

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    for key, td in themes.items():
        stocks = td.get("stocks", [])

        # 篩選
        if term:
            stocks = [
                s for s in stocks
                if term in s["code"].lower() or term in s["name"].lower()
            ]
        if not stocks:
            continue

        # 計算此主題即時均值
        changes = [
            live[s["code"]]["change_pct"]
            if s["code"] in live and live[s["code"]].get("change_pct") is not None
            else s.get("change_pct")
            for s in stocks
        ]
        changes = [c for c in changes if c is not None]
        avg_t   = sum(changes) / len(changes) if changes else None
        tc      = trend_color(avg_t)
        pct_lbl = fmt_pct(avg_t)

        accent = THEME_COLORS.get(key, "#38d1ff")
        label  = (
            f"{key}  "
            f"{'▲' if (avg_t or 0)>0 else '▼' if (avg_t or 0)<0 else '●'} "
            f"{pct_lbl}  ({len(stocks)} 檔)"
        )

        with st.expander(label, expanded=False):
            st.markdown(
                _stock_table_html(stocks, live),
                unsafe_allow_html=True,
            )

    # ── 頁尾 ───────────────────────────────────────────────────────────
    st.markdown(
        _footer_html(latest_date, updated_at, is_live),
        unsafe_allow_html=True,
    )

    # ── 自動重整（盤中）───────────────────────────────────────────────
    if is_live:
        time.sleep(20)
        st.rerun()


if __name__ == "__main__":
    main()
