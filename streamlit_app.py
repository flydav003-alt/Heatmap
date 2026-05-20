from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf


ROOT      = Path(__file__).resolve().parent
HTML_PATH = ROOT / "docs" / "index.html"

# yfinance 一次最多抓幾檔（太多會被 Yahoo rate limit）
YF_BATCH_SIZE = 50


# ── Helpers ────────────────────────────────────────────────────────────────────
def parse_float(value: Any) -> float | None:
    if value in (None, "", "-", "--", "---", "----", "N/A"):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None





def extract_symbols(base_html: str) -> list[dict[str, str]]:
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


# ── yfinance 批次抓取 ─────────────────────────────────────────────────────────
def fetch_yf_batch(codes: list[str]) -> dict[str, dict[str, Any]]:
    """用 yfinance 批次抓台股報價，代碼加 .TW（上市）或 .TWO（上櫃）。"""
    tickers_str = " ".join(f"{c}.TW" for c in codes)
    quotes: dict[str, dict[str, Any]] = {}
    try:
        data = yf.download(
            tickers_str,
            period="2d",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        now = datetime.now()
        latest_date = now.strftime("%Y-%m-%d")
        latest_time = now.strftime("%H:%M:%S")

        for code in codes:
            ticker = f"{code}.TW"
            try:
                if len(codes) == 1:
                    df = data
                else:
                    df = data[ticker]
                if df is None or df.empty or len(df) < 1:
                    continue
                close_today = float(df["Close"].iloc[-1])
                if len(df) >= 2:
                    prev = float(df["Close"].iloc[-2])
                    chg_pct = (close_today / prev - 1) * 100 if prev else None
                else:
                    chg_pct = None
                vol = float(df["Volume"].iloc[-1]) if "Volume" in df.columns else None
                # Yahoo 成交量單位是股，換算成張（1張=1000股）
                vol_lots = vol / 1000 if vol is not None else None
                quotes[code] = {
                    "price":       close_today,
                    "change_pct":  chg_pct,
                    "volume_lots": vol_lots,
                    "time":        latest_time,
                    "date":        latest_date,
                }
            except Exception:
                continue
    except Exception:
        pass
    return quotes


# ── 整合報價 ───────────────────────────────────────────────────────────────────
@st.cache_data(ttl=180, show_spinner=False)  # cache 3分鐘，避免重複打 Yahoo
def fetch_all_quotes(symbols: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    all_codes = [c for c, e in symbols]
    quotes: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    # 分批抓，每批 YF_BATCH_SIZE 檔
    for i in range(0, len(all_codes), YF_BATCH_SIZE):
        batch = all_codes[i : i + YF_BATCH_SIZE]
        try:
            batch_data = fetch_yf_batch(batch)
            quotes.update(batch_data)
        except Exception as exc:
            errors.append(f"yfinance batch {i // YF_BATCH_SIZE}: {exc}")
        time.sleep(0.5)

    # 整理時間
    latest_time = "--:--:--"
    latest_date = "--"
    for q in quotes.values():
        if q.get("time"):
            latest_time = str(q["time"])
        if q.get("date"):
            latest_date = str(q["date"])

    return {
        "quotes":          quotes,
        "latest_time":     latest_time,
        "latest_date":     latest_date,
        "fetched_count":   len(quotes),
        "requested_count": len(symbols),
        "errors":          errors,
        "using_fallback":  False,
    }


def estimate_page_height(base_html: str) -> int:
    num_rows   = base_html.count('data-code="')
    num_groups = base_html.count('class="group-card"')
    return max(4000, 1000 + num_groups * 68 + num_rows * 38)


def inject_live_script(base_html: str, payload: dict[str, Any]) -> str:
    live_json = json.dumps(payload, ensure_ascii=False)

    script = f"""
<script>
(() => {{
  "use strict";
  const payload = {live_json};
  const quotes  = payload.quotes || {{}};

  /* ── 格式化 ─────────────────────────────────────────────────────────── */
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

  /* ── 主迴圈 ──────────────────────────────────────────────────────────── */
  const groupStats = new Map();
  let totalChange = 0, totalCount = 0, totalVolume = 0;

  document.querySelectorAll("tr[data-code]").forEach(row => {{
    const code  = row.dataset.code;
    const quote = quotes[code];

    /* change_pct 優先取 API 值；若 API 未給（非交易時間）才 fallback 到
       靜態 HTML 的 data-change（build 時的收盤漲跌幅），確保熱力圖永遠有色彩 */
    let price     = (quote && quote.price      != null) ? +quote.price      : null;
    let changePct = (quote && quote.change_pct != null) ? +quote.change_pct : null;
    let volume    = (quote && quote.volume_lots != null) ? +quote.volume_lots : null;

    if (!isFinite(price))     price     = null;
    if (!isFinite(changePct)) changePct = null;
    if (!isFinite(volume))    volume    = null;

    // fallback：API 無即時 change_pct → 用 HTML data-change
    if (changePct == null) {{
      const raw = parseFloat(row.dataset.change);
      if (isFinite(raw)) changePct = raw;
    }}

    // 更新 dataset（排序用）
    if (price     != null) row.dataset.price  = price;
    if (changePct != null) row.dataset.change = changePct;
    if (volume    != null) row.dataset.volume = volume;

    // 更新表格 TD
    const nums = row.querySelectorAll("td.num");
    if (price     != null && nums[0]) nums[0].textContent = fmtPrice(price);
    if (changePct != null && nums[1]) {{
      nums[1].textContent = fmtPct(changePct);
      nums[1].className   = `num ${{trend(changePct)}}`;
    }}
    if (volume    != null && nums[2]) nums[2].textContent = fmtInt(volume);

    // 累計 group 統計
    const group = row.dataset.group || "";
    if (!groupStats.has(group)) groupStats.set(group, {{sum:0, count:0, volume:0}});
    const stat = groupStats.get(group);
    if (changePct != null) {{ stat.sum += changePct; stat.count++; totalChange += changePct; totalCount++; }}
    if (volume    != null) {{ stat.volume += volume; totalVolume += volume; }}
  }});

  /* ── KPI 列 ──────────────────────────────────────────────────────────── */
  const avg = totalCount ? totalChange / totalCount : null;
  const kpiValues = document.querySelectorAll(".kpi .value");
  if (kpiValues[2]) {{ kpiValues[2].textContent = fmtPct(avg); kpiValues[2].className = `value ${{trend(avg)}}`; }}
  if (kpiValues[3]) kpiValues[3].textContent = fmtInt(totalVolume) + "張";
  if (kpiValues[4]) kpiValues[4].textContent = payload.latest_time || "--:--:--";

  /* ── Toolbar meta ────────────────────────────────────────────────────── */
  const meta = document.querySelector(".toolbar-meta");
  if (meta) meta.innerHTML =
    `共 <b>${{payload.requested_count}}</b> 檔 ` +
    `| 已抓 <b>${{payload.fetched_count}}</b> 檔 ` +
    `| 即時 <b>${{payload.latest_date}} ${{payload.latest_time}}</b>`;

  /* ── Group chips ─────────────────────────────────────────────────────── */
  document.querySelectorAll(".group-card").forEach(card => {{
    const stat = groupStats.get(card.dataset.group);
    if (!stat || !stat.count) return;
    const a    = stat.sum / stat.count;
    const chip = card.querySelector(".group-chip");
    if (chip) {{ chip.textContent = fmtPct(a); chip.className = `group-chip ${{trend(a)}}`; }}
  }});

  /* ── Stage 熱力格 ────────────────────────────────────────────────────── */
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
    if (ve) ve.textContent = fmtInt(stat.volume) + " 張";
  }});

  /* ── 成交量排行 ──────────────────────────────────────────────────────── */
  const leaderboard = document.getElementById("vol-leaderboard");
  if (leaderboard) {{
    const ranked = Array.from(document.querySelectorAll("tr[data-code]"))
      .map(row => {{
        const q   = quotes[row.dataset.code];
        const vol = q && q.volume_lots != null ? +q.volume_lots : null;
        if (!vol || !isFinite(vol)) return null;
        let chg = (q && q.change_pct != null && isFinite(+q.change_pct))
          ? +q.change_pct : parseFloat(row.dataset.change);
        if (!isFinite(chg)) chg = null;
        return {{
          code: row.dataset.code,
          name: row.dataset.name || row.dataset.code,
          vol, chg,
          price: q && q.price != null ? +q.price : null,
        }};
      }})
      .filter(Boolean)
      .sort((a, b) => b.vol - a.vol)
      .slice(0, 12);

    leaderboard.innerHTML = ranked.map(s => `
      <div class="vol-card">
        <div class="vol-top">
          <span class="vol-code">${{s.code}}</span>
          <span class="vol-chg ${{trend(s.chg)}}">${{fmtPct(s.chg)}}</span>
        </div>
        <div class="vol-name">${{s.name}}</div>
        <div class="vol-vol">${{fmtInt(s.vol)}} 張</div>
      </div>`).join("");
  }}
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
    symbols = tuple(
        (item["code"], item["exchange"]) for item in extract_symbols(html_content)
    )

    if not symbols:
        st.error("HTML 裡沒有找到任何 data-code，靜態檔案可能有問題，請重新執行 build 腳本。")
        st.stop()

    with st.spinner(f"正在抓取 {len(symbols)} 檔即時報價…"):
        payload = fetch_all_quotes(symbols)



    if payload["fetched_count"] == 0:
        st.warning("⚠️ 報價暫時無法取得（非交易時間，或 Yahoo Finance 暫時無回應）。頁面顯示靜態快照。")
    if payload["errors"]:
        with st.expander("API 錯誤詳情", expanded=False):
            for e in payload["errors"]:
                st.caption(e)

    html_content = inject_live_script(html_content, payload)
    page_height  = estimate_page_height(html_content)
    components.html(html_content, height=page_height, scrolling=False)


if __name__ == "__main__":
    main()
