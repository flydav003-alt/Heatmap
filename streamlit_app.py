from __future__ import annotations

import html
import json
import math
import re
import ssl
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components


ROOT = Path(__file__).resolve().parent
HTML_PATH = ROOT / "docs" / "index.html"
TWSE_MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
BATCH_SIZE = 90
SSL_CONTEXT = ssl._create_unverified_context()


def parse_float(value: Any) -> float | None:
    if value in (None, "", "-", "--", "----"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def extract_symbols(base_html: str) -> list[dict[str, str]]:
    pattern = re.compile(
        r'<tr data-code="(?P<code>\d{4})"[\s\S]*?'
        r'<span class="market">(?P<market>.*?)</span>',
        re.S,
    )
    seen: set[str] = set()
    symbols: list[dict[str, str]] = []
    for match in pattern.finditer(base_html):
        code = match.group("code")
        if code in seen:
            continue
        seen.add(code)
        market_text = re.sub(r"<.*?>", "", html.unescape(match.group("market")))
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
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://mis.twse.com.tw/stock/index.jsp",
        },
    )
    with urllib.request.urlopen(request, timeout=20, context=SSL_CONTEXT) as response:
        payload = json.loads(response.read().decode("utf-8-sig"))

    quotes: dict[str, dict[str, Any]] = {}
    for item in payload.get("msgArray", []):
        code = str(item.get("c", "")).strip()
        price = parse_float(item.get("z"))
        previous_close = parse_float(item.get("y"))
        volume_lots = parse_float(item.get("v"))
        if price is None:
            price = parse_float(item.get("pz")) or previous_close
        change_pct = None
        if price is not None and previous_close not in (None, 0):
            change_pct = (price / previous_close - 1) * 100
        if code:
            quotes[code] = {
                "price": price,
                "change_pct": change_pct,
                "volume_lots": volume_lots,
                "time": item.get("t") or payload.get("queryTime", {}).get("sysTime"),
                "date": item.get("d") or payload.get("queryTime", {}).get("sysDate"),
            }
    return quotes


@st.cache_data(ttl=20, show_spinner=False)
def fetch_twse_quotes(symbols: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    items = [{"code": code, "exchange": exchange} for code, exchange in symbols]
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
    for quote in quotes.values():
        if quote.get("time"):
            latest_time = str(quote["time"])
        if quote.get("date"):
            latest_date = str(quote["date"])
    if re.fullmatch(r"\d{8}", latest_date):
        latest_date = f"{latest_date[:4]}-{latest_date[4:6]}-{latest_date[6:8]}"

    return {
        "quotes": quotes,
        "latest_time": latest_time,
        "latest_date": latest_date,
        "fetched_count": len(quotes),
        "requested_count": len(items),
        "errors": errors,
    }


def inject_live_script(base_html: str, payload: dict[str, Any]) -> str:
    live_json = json.dumps(payload, ensure_ascii=False)
    script = f"""
<script>
(() => {{
  const payload = {live_json};
  const quotes = payload.quotes || {{}};

  const fmtPrice = (value) => {{
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
    return Number(value).toLocaleString("en-US", {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
  }};
  const fmtInt = (value) => {{
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
    return Math.round(Number(value)).toLocaleString("en-US");
  }};
  const fmtPct = (value) => {{
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
    const sign = Number(value) > 0 ? "+" : "";
    return `${{sign}}${{Number(value).toFixed(2)}}%`;
  }};
  const trend = (value) => {{
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "na";
    if (Number(value) > 0) return "up";
    if (Number(value) < 0) return "down";
    return "flat";
  }};
  const heat = (value) => {{
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "rgba(100,116,139,.18)";
    const strength = Math.min(Math.abs(Number(value)) / 6, 1);
    const alpha = 0.18 + strength * 0.38;
    return Number(value) > 0 ? `rgba(244,63,94,${{alpha.toFixed(3)}})` : `rgba(34,197,94,${{alpha.toFixed(3)}})`;
  }};

  const groupStats = new Map();
  let totalChange = 0;
  let totalCount = 0;
  let totalVolume = 0;

  document.querySelectorAll("tr[data-code]").forEach((row) => {{
    const code = row.dataset.code;
    const quote = quotes[code];
    if (!quote) return;

    const price = Number(quote.price);
    const change = Number(quote.change_pct);
    const volume = Number(quote.volume_lots);
    row.dataset.price = Number.isFinite(price) ? String(price) : row.dataset.price;
    row.dataset.change = Number.isFinite(change) ? String(change) : row.dataset.change;
    row.dataset.volume = Number.isFinite(volume) ? String(volume) : row.dataset.volume;

    const nums = row.querySelectorAll("td.num");
    if (nums[0]) nums[0].textContent = fmtPrice(price);
    if (nums[1]) {{
      nums[1].textContent = fmtPct(change);
      nums[1].className = `num ${{trend(change)}}`;
    }}
    if (nums[2]) nums[2].textContent = fmtInt(volume);

    const group = row.dataset.group || "";
    if (!groupStats.has(group)) groupStats.set(group, {{ sum: 0, count: 0, volume: 0 }});
    const stat = groupStats.get(group);
    if (Number.isFinite(change)) {{
      stat.sum += change;
      stat.count += 1;
      totalChange += change;
      totalCount += 1;
    }}
    if (Number.isFinite(volume)) {{
      stat.volume += volume;
      totalVolume += volume;
    }}
  }});

  const avgChange = totalCount ? totalChange / totalCount : null;
  const kpiValues = document.querySelectorAll(".kpi .value");
  if (kpiValues[2]) {{
    kpiValues[2].textContent = fmtPct(avgChange);
    kpiValues[2].className = `value ${{trend(avgChange)}}`;
  }}
  if (kpiValues[3]) kpiValues[3].textContent = `${{fmtInt(totalVolume)}}張`;
  if (kpiValues[4]) kpiValues[4].textContent = payload.latest_time || "--:--:--";

  const toolbarMeta = document.querySelector(".toolbar-meta");
  if (toolbarMeta) {{
    toolbarMeta.innerHTML = `共 <b>${{payload.requested_count}}</b> 檔 | 已抓 <b>${{payload.fetched_count}}</b> 檔 | 即時基準 <b>${{payload.latest_date}} ${{payload.latest_time}}</b>`;
  }}

  document.querySelectorAll(".group-card").forEach((card) => {{
    const group = card.dataset.group;
    const stat = groupStats.get(group);
    if (!stat || !stat.count) return;
    const avg = stat.sum / stat.count;
    const chip = card.querySelector(".group-chip");
    if (chip) {{
      chip.textContent = fmtPct(avg);
      chip.className = `group-chip ${{trend(avg)}}`;
    }}
  }});

  document.querySelectorAll(".stage-heat-cell").forEach((cell) => {{
    const group = cell.dataset.filter;
    const stat = groupStats.get(group);
    if (!stat || !stat.count) return;
    const avg = stat.sum / stat.count;
    cell.classList.remove("up", "down", "flat", "na");
    cell.classList.add(trend(avg));
    cell.style.setProperty("--heat", heat(avg));
    const changeEl = cell.querySelector(".stage-heat-change");
    const volumeEl = cell.querySelector(".stage-heat-volume");
    if (changeEl) changeEl.textContent = fmtPct(avg);
    if (volumeEl) volumeEl.textContent = `${{fmtInt(stat.volume)}} 張`;
  }});
}})();
</script>
"""
    return base_html.replace("</body>", f"{script}</body>")


def main() -> None:
    st.set_page_config(
        page_title="Taiwan Semiconductor AI Heatmap",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    html_content = HTML_PATH.read_text(encoding="utf-8")
    symbols = tuple((item["code"], item["exchange"]) for item in extract_symbols(html_content))

    payload = fetch_twse_quotes(symbols)
    if payload["fetched_count"] == 0:
        st.error("TWSE MIS 即時資料沒有抓到任何股票，請稍後再重新整理。")
        if payload["errors"]:
            st.caption(payload["errors"][0])
        return

    html_content = inject_live_script(html_content, payload)

    st.markdown(
        """
        <style>
          .block-container { padding-top: 0.2rem; padding-bottom: 0; max-width: 100%; }
          [data-testid="stHeader"] { background: transparent; }
          [data-testid="stSidebar"] { display: none; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    components.html(html_content, height=4200, scrolling=True)


if __name__ == "__main__":
    main()
