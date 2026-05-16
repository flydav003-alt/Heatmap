from __future__ import annotations

import html
import json
import re
import ssl
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components


ROOT      = Path(__file__).resolve().parent
HTML_PATH = ROOT / "docs" / "index.html"
TWSE_MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
BATCH_SIZE   = 90
SSL_CONTEXT  = ssl._create_unverified_context()


# ── Helpers ────────────────────────────────────────────────────────────────────
def parse_float(value: Any) -> float | None:
    if value in (None, "", "-", "--", "----"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


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


def fetch_twse_batch(batch: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    ex_ch = "|".join(f'{item["exchange"]}_{item["code"]}.tw' for item in batch)
    query = urllib.parse.urlencode(
        {"ex_ch": ex_ch, "json": "1", "delay": "0", "_": str(int(time.time() * 1000))},
        safe="|_.",
    )
    url = f"{TWSE_MIS_URL}?{query}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://mis.twse.com.tw/stock/index.jsp",
        },
    )
    with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT) as resp:
        payload = json.loads(resp.read().decode("utf-8-sig"))

    quotes: dict[str, dict[str, Any]] = {}
    for item in payload.get("msgArray", []):
        code         = str(item.get("c", "")).strip()
        price        = parse_float(item.get("z"))
        prev_close   = parse_float(item.get("y"))
        volume_lots  = parse_float(item.get("v"))
        if price is None:
            price = parse_float(item.get("pz")) or prev_close
        change_pct = None
        if price is not None and prev_close not in (None, 0):
            change_pct = (price / prev_close - 1) * 100
        if code:
            quotes[code] = {
                "price":       price,
                "change_pct":  change_pct,
                "volume_lots": volume_lots,
                "time": item.get("t") or payload.get("queryTime", {}).get("sysTime"),
                "date": item.get("d") or payload.get("queryTime", {}).get("sysDate"),
            }
    return quotes


@st.cache_data(ttl=20, show_spinner=False)
def fetch_twse_quotes(symbols: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    items  = [{"code": code, "exchange": exchange} for code, exchange in symbols]
    quotes: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for index in range(0, len(items), BATCH_SIZE):
        batch = items[index : index + BATCH_SIZE]
        try:
            quotes.update(fetch_twse_batch(batch))
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
        time.sleep(0.35)

    latest_time = "--:--:--"
    latest_date = "--"
    for q in quotes.values():
        if q.get("time"):
            latest_time = str(q["time"])
        if q.get("date"):
            latest_date = str(q["date"])
    if re.fullmatch(r"\d{8}", latest_date):
        latest_date = f"{latest_date[:4]}-{latest_date[4:6]}-{latest_date[6:8]}"

    return {
        "quotes":          quotes,
        "latest_time":     latest_time,
        "latest_date":     latest_date,
        "fetched_count":   len(quotes),
        "requested_count": len(items),
        "errors":          errors,
    }


def estimate_page_height(base_html: str) -> int:
    """
    Dynamically estimate iframe height so the page is never clipped.
    Old code used a hardcoded 4200 px which cut off at ~1/4 of the real content.
    """
    num_rows   = base_html.count('data-code="')
    num_groups = base_html.count('class="group-card"')
    # header + pills + stage-heatmap + toolbar ≈ 1 000 px
    # each group card header ≈ 68 px
    # each table row          ≈ 38 px
    estimated  = 1000 + num_groups * 68 + num_rows * 38
    return max(4000, estimated)


def inject_live_script(base_html: str, payload: dict[str, Any]) -> str:
    live_json = json.dumps(payload, ensure_ascii=False)
    script = f"""
<script>
(() => {{
  const payload = {live_json};
  const quotes  = payload.quotes || {{}};

  const fmtPrice = v => (v==null||isNaN(+v)) ? "--" : (+v).toLocaleString("en-US",{{minimumFractionDigits:2,maximumFractionDigits:2}});
  const fmtInt   = v => (v==null||isNaN(+v)) ? "--" : Math.round(+v).toLocaleString("en-US");
  const fmtPct   = v => {{
    if (v==null||isNaN(+v)) return "--";
    const sign = +v>0?"+":"";
    return `${{sign}}${{(+v).toFixed(2)}}%`;
  }};
  const trend = v => (v==null||isNaN(+v)) ? "na" : +v>0?"up":+v<0?"down":"flat";
  const heat  = v => {{
    if (v==null||isNaN(+v)) return "rgba(12,22,40,0.25)";
    const s = Math.min(Math.abs(+v)/6,1);
    const a = (0.14+s*0.46).toFixed(3);
    return +v>0 ? `rgba(255,45,84,${{a}})` : `rgba(0,210,110,${{a}})`;
  }};

  const groupStats = new Map();
  let totalChange=0, totalCount=0, totalVolume=0;

  document.querySelectorAll("tr[data-code]").forEach(row => {{
    const code  = row.dataset.code;
    const quote = quotes[code];
    if (!quote) return;
    const price  = +quote.price;
    const change = +quote.change_pct;
    const volume = +quote.volume_lots;
    if (isFinite(price))  row.dataset.price  = price;
    if (isFinite(change)) row.dataset.change = change;
    if (isFinite(volume)) row.dataset.volume = volume;

    const nums = row.querySelectorAll("td.num");
    if (nums[0]) nums[0].textContent = fmtPrice(price);
    if (nums[1]) {{ nums[1].textContent = fmtPct(change); nums[1].className = `num ${{trend(change)}}`; }}
    if (nums[2]) nums[2].textContent = fmtInt(volume);

    const group = row.dataset.group || "";
    if (!groupStats.has(group)) groupStats.set(group, {{sum:0,count:0,volume:0}});
    const stat = groupStats.get(group);
    if (isFinite(change)) {{ stat.sum+=change; stat.count+=1; totalChange+=change; totalCount+=1; }}
    if (isFinite(volume)) {{ stat.volume+=volume; totalVolume+=volume; }}
  }});

  // ── KPI bar ──────────────────────────────────────────────────────────────
  const avg = totalCount ? totalChange/totalCount : null;
  const kpiValues = document.querySelectorAll(".kpi .value");
  if (kpiValues[2]) {{ kpiValues[2].textContent = fmtPct(avg); kpiValues[2].className=`value ${{trend(avg)}}`; }}
  if (kpiValues[3]) kpiValues[3].textContent = fmtInt(totalVolume)+"張";
  if (kpiValues[4]) kpiValues[4].textContent = payload.latest_time||"--:--:--";

  // ── Toolbar meta ─────────────────────────────────────────────────────────
  const meta = document.querySelector(".toolbar-meta");
  if (meta) meta.innerHTML =
    `共 <b>${{payload.requested_count}}</b> 檔 | 已抓 <b>${{payload.fetched_count}}</b> 檔 | 即時 <b>${{payload.latest_date}} ${{payload.latest_time}}</b>`;

  // ── Group chips ───────────────────────────────────────────────────────────
  document.querySelectorAll(".group-card").forEach(card => {{
    const stat = groupStats.get(card.dataset.group);
    if (!stat||!stat.count) return;
    const avg  = stat.sum/stat.count;
    const chip = card.querySelector(".group-chip");
    if (chip) {{ chip.textContent=fmtPct(avg); chip.className=`group-chip ${{trend(avg)}}`; }}
  }});

  // ── Stage heatmap ─────────────────────────────────────────────────────────
  document.querySelectorAll(".stage-heat-cell").forEach(cell => {{
    const stat = groupStats.get(cell.dataset.filter);
    if (!stat||!stat.count) return;
    const avg = stat.sum/stat.count;
    cell.classList.remove("up","down","flat","na");
    cell.classList.add(trend(avg));
    cell.style.setProperty("--heat", heat(avg));
    const ce = cell.querySelector(".stage-heat-change");
    const ve = cell.querySelector(".stage-heat-volume");
    if (ce) ce.textContent = fmtPct(avg);
    if (ve) ve.textContent = fmtInt(stat.volume)+" 張";
  }});

  // ── 成交量排行 ────────────────────────────────────────────────────────────
  const leaderboard = document.getElementById("vol-leaderboard");
  if (leaderboard) {{
    // 收集所有有即時報價的股票，排序取前 12
    const allRows = Array.from(document.querySelectorAll("tr[data-code]"));
    const ranked = allRows
      .map(row => {{
        const code   = row.dataset.code;
        const q      = quotes[code];
        if (!q) return null;
        const vol    = +q.volume_lots;
        const change = +q.change_pct;
        const price  = +q.price;
        const name   = row.dataset.name || code;
        const group  = row.dataset.group || "";
        return isFinite(vol) ? {{code,name,group,vol,change,price}} : null;
      }})
      .filter(Boolean)
      .sort((a,b) => b.vol - a.vol)
      .slice(0, 12);

    leaderboard.innerHTML = ranked.map(s => {{
      const tc  = trend(s.change);
      const pct = fmtPct(s.change);
      const vol = fmtInt(s.vol);
      return `<div class="vol-card">
        <div class="vol-top">
          <span class="vol-code">${{s.code}}</span>
          <span class="vol-chg ${{tc}}">${{pct}}</span>
        </div>
        <div class="vol-name">${{s.name}}</div>
        <div class="vol-vol">${{vol}} 張</div>
      </div>`;
    }}).join("");
  }}
}})();
</script>
"""
    # vol_html is a plain string (no f-string), so CSS braces are literal { }
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
    # 把 vol_html 插在 <main id="groupList"> 前面（toolbar 和股票列表之間）
    # script 插在 </body> 前（需要等 DOM 完整才能讀 tr[data-code]）
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

    # 去掉 Streamlit 預設 padding 和側欄
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

    # ── 檢查靜態 HTML ──────────────────────────────────────────────────────
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

    # ── 抓即時報價 ─────────────────────────────────────────────────────────
    with st.spinner(f"正在抓取 {len(symbols)} 檔即時報價…"):
        payload = fetch_twse_quotes(symbols)

    if payload["fetched_count"] == 0:
        st.warning(
            "⚠️ TWSE MIS 即時報價暫時無法取得（非交易時間，或 API 維護中）。\n"
            "頁面將顯示靜態快照資料。"
        )
        if payload["errors"]:
            with st.expander("錯誤詳情"):
                for e in payload["errors"]:
                    st.caption(e)

    html_content = inject_live_script(html_content, payload)

    # ── 動態計算 iframe 高度，避免內容被截斷 ──────────────────────────────
    page_height = estimate_page_height(html_content)
    components.html(html_content, height=page_height, scrolling=False)


if __name__ == "__main__":
    main()
