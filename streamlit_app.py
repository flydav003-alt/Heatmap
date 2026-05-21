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


# ── 整合報價（改用批次下載，避免逐一 rate limit）────────────────────────────
@st.cache_data(ttl=180, show_spinner=False)
def fetch_all_quotes(symbols: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    """
    改用 yf.download 批次下載，一次取得所有股票的最新報價，
    大幅降低 rate limit 風險並加速取得時間。
    """
    import pandas as pd

    # 建立 ticker 對照表
    code_to_exchange = {code: exchange for code, exchange in symbols}
    all_codes = list(code_to_exchange.keys())

    # 組合 ticker 清單（.TW 和 .TWO 都嘗試）
    tw_tickers  = [f"{c}.TW"  for c in all_codes]
    two_tickers = [f"{c}.TWO" for c in all_codes]

    quotes: dict[str, dict[str, Any]] = {}
    now = datetime.now()
    latest_time = now.strftime("%H:%M:%S")
    latest_date = now.strftime("%Y-%m-%d")

    def _batch_download(tickers: list[str]) -> Any:
        try:
            raw = yf.download(
                tickers=tickers,
                period="2d",          # 只需最近 2 天取到今日/昨日收盤
                group_by="ticker",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
            return raw
        except Exception as exc:
            print(f"[yf] 批次下載失敗: {exc}")
            return None

    def _extract_quote(raw: Any, ticker: str, code: str) -> dict[str, Any] | None:
        try:
            if raw is None or raw.empty:
                return None
            # 多 ticker 時 raw 是 MultiIndex；單一 ticker 時是普通 DataFrame
            if isinstance(raw.columns, pd.MultiIndex):
                if ticker not in raw.columns.get_level_values(0):
                    return None
                df = raw[ticker]
            else:
                df = raw

            if df is None or df.empty:
                return None

            close  = df["Close"].dropna()
            volume = df["Volume"].dropna()

            if len(close) < 1:
                return None

            price = float(close.iloc[-1])
            if price == 0 or not pd.notna(price):
                return None

            # 計算漲跌幅：今日 vs 昨日收盤
            prev_close = float(close.iloc[-2]) if len(close) >= 2 else None
            chg_pct = ((price / prev_close - 1) * 100) if prev_close and prev_close != 0 else None

            # 成交量（股 → 張）
            vol_shares = float(volume.iloc[-1]) if len(volume) >= 1 else None
            vol_lots   = vol_shares / 1000 if vol_shares else None

            return {
                "price":       price,
                "change_pct":  chg_pct,
                "volume_lots": vol_lots,
                "time":        latest_time,
                "date":        latest_date,
            }
        except Exception:
            return None

    # 先批次下載 .TW
    raw_tw = _batch_download(tw_tickers)
    # 再批次下載 .TWO
    raw_two = _batch_download(two_tickers)

    for code in all_codes:
        # 優先取 .TW，fallback 到 .TWO
        q = _extract_quote(raw_tw,  f"{code}.TW",  code)
        if q is None:
            q = _extract_quote(raw_two, f"{code}.TWO", code)
        if q is not None:
            quotes[code] = q

    return {
        "quotes":          quotes,
        "latest_time":     latest_time,
        "latest_date":     latest_date,
        "fetched_count":   len(quotes),
        "requested_count": len(symbols),
        "errors":          [],
        "using_fallback":  False,
    }


def estimate_page_height(base_html: str) -> int:
    num_rows   = base_html.count('data-code="')
    num_groups = base_html.count('class="group-card"')
    return max(4000, 1000 + num_groups * 68 + num_rows * 38)


# ── GROUP META for JS (stage & color mapping) ──────────────────────────────────
GROUP_STAGE_MAP = {
    "IC設計 / IP / ASIC":      "上游",
    "晶圓代工 / 功率半導體":    "上游",
    "記憶體 / HBM":             "上游",
    "矽晶圓 / 材料設備 / 廠務": "上游",
    "先進封裝 / CoWoS":         "中游",
    "封測 / 測試介面":           "中游",
    "PCB / 載板 / CCL":         "中游",
    "被動元件":                  "中游",
    "散熱":                      "中游",
    "電源 / BBU":                "中游",
    "高速互連 / 連接器 / 線材": "中游",
    "AI伺服器 / 機櫃組裝":      "下游",
    "網通 / 光通訊 / CPO":      "下游",
    "低軌衛星 / SpaceX":        "下游",
    "半導體其他":                "補充",
}

CORE_UPSTREAM    = {"IC設計 / IP / ASIC", "晶圓代工 / 功率半導體"}
LAGGARD_GROUPS   = {"被動元件"}
DOWNSTREAM_GROUPS = {"AI伺服器 / 機櫃組裝", "網通 / 光通訊 / CPO"}


def inject_live_script(base_html: str, payload: dict[str, Any]) -> str:
    live_json          = json.dumps(payload, ensure_ascii=False)
    stage_map_json     = json.dumps(GROUP_STAGE_MAP, ensure_ascii=False)
    core_upstream_json = json.dumps(list(CORE_UPSTREAM), ensure_ascii=False)
    laggard_json       = json.dumps(list(LAGGARD_GROUPS), ensure_ascii=False)
    downstream_json    = json.dumps(list(DOWNSTREAM_GROUPS), ensure_ascii=False)

    script = f"""
<script>
(() => {{
  "use strict";
  const payload = {live_json};
  const quotes  = payload.quotes || {{}};

  const GROUP_STAGE  = {stage_map_json};
  const CORE_UP      = new Set({core_upstream_json});
  const LAGGARD_GRP  = new Set({laggard_json});
  const DOWNSTREAM   = new Set({downstream_json});

  /* ── 格式化 ─────────────────────────────────────────────────────────────── */
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

  const safe = v => {{
    const n = parseFloat(v);
    return (isFinite(n) && n !== 0) ? n : null;  // 0 也視為無效，避免除以 0
  }};

  /* ── 主迴圈：掃描所有個股行，計算各族群指標 ─────────────────────────────── */
  const groupStats = new Map();
  let totalChange = 0, totalCount = 0, totalVolume = 0;

  document.querySelectorAll("tr[data-code]").forEach(row => {{
    const code  = row.dataset.code;
    const quote = quotes[code];

    let price     = (quote && quote.price      != null) ? +quote.price      : null;
    let changePct = (quote && quote.change_pct != null) ? +quote.change_pct : null;
    let volume    = (quote && quote.volume_lots != null) ? +quote.volume_lots : null;

    if (!isFinite(price))     price     = null;
    if (!isFinite(changePct)) changePct = null;
    if (!isFinite(volume))    volume    = null;

    // Fallback：API 無即時 change_pct → 用 HTML data-change
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

    // 讀取預埋的歷史 dataset 屬性
    // HTML attr: data-avg-vol20 → JS: row.dataset.avgVol20
    // HTML attr: data-p5-close  → JS: row.dataset.p5Close
    // HTML attr: data-p20-high  → JS: row.dataset.p20High
    const avgVol20 = safe(row.dataset.avgVol20);
    const p5close  = safe(row.dataset.p5Close);
    const p20high  = safe(row.dataset.p20High);

    // 累計族群統計
    const group = row.dataset.group || "";
    if (!groupStats.has(group)) {{
      groupStats.set(group, {{
        sum: 0, count: 0, volume: 0,
        upCount: 0,
        totalCount: 0,
        sumAvgVol20: 0, cntVol20: 0,
        sumPrice: 0,    cntPrice: 0,
        sumP5close: 0,  cntP5: 0,
        sumP20high: 0,  cntP20: 0,
      }});
    }}
    const stat = groupStats.get(group);

    if (changePct != null) {{
      stat.sum += changePct;
      stat.count++;
      stat.totalCount++;
      if (changePct > 0) stat.upCount++;
      totalChange += changePct;
      totalCount++;
    }}
    if (volume != null) {{ stat.volume += volume; totalVolume += volume; }}

    if (avgVol20 != null) {{ stat.sumAvgVol20 += avgVol20; stat.cntVol20++; }}
    if (price    != null) {{ stat.sumPrice   += price;   stat.cntPrice++; }}
    if (p5close  != null) {{ stat.sumP5close += p5close; stat.cntP5++; }}
    if (p20high  != null) {{ stat.sumP20high += p20high; stat.cntP20++; }}
  }});

  /* ── 計算族群四大指標 + 輪動等級 ─────────────────────────────────────────── */
  /*
   * 關鍵修正：null 值應視為「條件不通過」，而非 fallback 到「通過」。
   *
   * 等級判定（嚴格模式）：
   * A: avgChange > 1.2% AND breadth >= 65% AND (volRatio >= 1.35 OR volRatio 無資料但 volume 有值)
   * B: avgChange > 0.6% AND breadth >= 50% AND (volRatio >= 1.15 OR volRatio 無資料但 volume 有值)
   * C: volRatio >= 1.35（有資料且確認） AND drawdown < -10% AND mom5d < 3%
   * E: avgChange < -1.0% OR breadth < 25%（有資料時才觸發）
   * D: 其餘
   *
   * volRatio 的 null fallback 策略：
   * - 若 cntVol20 == 0（build 時 yfinance 沒跑到），則無法判量比，
   *   A/B 級只靠漲幅+廣度，設寬鬆門檻（avgChange > 2% / > 1% 才給）
   * - C 級嚴格要求 volRatio 有值才判定
   */

  const groupGrades = new Map();

  groupStats.forEach((stat, group) => {{
    if (!stat.count) return;

    const avgChange = stat.sum / stat.count;

    // 廣度（有報價者才算）
    const breadth = stat.totalCount > 0
      ? (stat.upCount / stat.totalCount) * 100
      : null;

    // 量比（需要歷史均量資料）
    const avgVol20perStock = stat.cntVol20 > 0
      ? stat.sumAvgVol20 / stat.cntVol20
      : null;
    // 族群預估20日總均量 = 每股均量 * 有均量資料的股數
    const estTotalVol20 = avgVol20perStock != null
      ? avgVol20perStock * stat.cntVol20
      : null;
    const volRatio = (estTotalVol20 != null && estTotalVol20 > 0 && stat.volume > 0)
      ? stat.volume / estTotalVol20
      : null;

    // 5日強弱度 & 20日位階
    const avgPrice   = stat.cntPrice > 0 ? stat.sumPrice   / stat.cntPrice : null;
    const avgP5close = stat.cntP5    > 0 ? stat.sumP5close / stat.cntP5    : null;
    const avgP20high = stat.cntP20   > 0 ? stat.sumP20high / stat.cntP20   : null;

    const mom5d    = (avgPrice != null && avgP5close != null && avgP5close > 0)
      ? (avgPrice / avgP5close - 1) * 100 : null;
    const drawdown = (avgPrice != null && avgP20high != null && avgP20high > 0)
      ? (avgPrice / avgP20high - 1) * 100 : null;

    // ── 等級判定（嚴格版：null 不視為通過）────────────────────────────────
    const hasVolData = volRatio != null;

    let grade = "D";

    // E 級：退潮（優先判定）
    if (
      avgChange < -1.0 ||
      (breadth != null && breadth < 25)
    ) {{
      grade = "E";
    }}
    // A 級：領漲爆發
    else if (
      avgChange > 1.2 &&
      breadth != null && breadth >= 65 &&
      (hasVolData ? volRatio >= 1.35 : avgChange > 2.0)  // 無量比資料時需更高漲幅才給A
    ) {{
      grade = "A";
    }}
    // B 級：擴散接棒
    else if (
      avgChange > 0.6 &&
      breadth != null && breadth >= 50 &&
      (hasVolData ? volRatio >= 1.15 : avgChange > 1.2)  // 無量比資料時需更高漲幅才給B
    ) {{
      grade = "B";
    }}
    // C 級：低基期補漲（嚴格要求量比有資料）
    else if (
      hasVolData && volRatio >= 1.35 &&
      drawdown != null && drawdown < -10 &&
      mom5d    != null && mom5d    < 3
    ) {{
      grade = "C";
    }}
    // D 級：橫盤整理（預設）

    const stage = GROUP_STAGE[group] || "";
    groupGrades.set(group, {{
      grade, stage,
      avgChange, breadth, volRatio, mom5d, drawdown,
    }});
  }});

  /* ── KPI 列 ─────────────────────────────────────────────────────────────── */
  const avg = totalCount ? totalChange / totalCount : null;
  const kpiValues = document.querySelectorAll(".kpi .value");
  if (kpiValues[2]) {{ kpiValues[2].textContent = fmtPct(avg); kpiValues[2].className = `value ${{trend(avg)}}`; }}
  if (kpiValues[3]) kpiValues[3].textContent = fmtInt(totalVolume) + "張";
  if (kpiValues[4]) kpiValues[4].textContent = payload.latest_time || "--:--:--";

  /* ── Toolbar meta ────────────────────────────────────────────────────────── */
  const meta = document.querySelector(".toolbar-meta");
  if (meta) meta.innerHTML =
    `共 <b>${{payload.requested_count}}</b> 檔 ` +
    `| 已抓 <b>${{payload.fetched_count}}</b> 檔 ` +
    `| 即時 <b>${{payload.latest_date}} ${{payload.latest_time}}</b>`;

  /* ── Group chips + rotation info ─────────────────────────────────────────── */
  const STAGE_LABEL = {{"上游": "上游", "中游": "中游", "下游": "下游", "補充": "補充"}};
  const GRADE_STAGE_SUFFIX = {{
    "A": "領漲", "B": "擴散", "C": "補漲", "D": "", "E": "退潮"
  }};

  document.querySelectorAll(".group-card").forEach(card => {{
    const stat  = groupStats.get(card.dataset.group);
    const ginfo = groupGrades.get(card.dataset.group);
    if (!stat || !stat.count) return;

    const a    = stat.sum / stat.count;
    const chip = card.querySelector(".group-chip");
    if (chip) {{
      chip.textContent = fmtPct(a);
      chip.className   = `group-chip ${{trend(a)}}`;
    }}

    let infoEl = card.querySelector(".group-rotation-info");
    if (!infoEl) {{
      infoEl = document.createElement("span");
      infoEl.className = "group-rotation-info";
      const gRight = card.querySelector(".g-right");
      if (gRight) gRight.insertBefore(infoEl, gRight.firstChild);
    }}
    if (ginfo) {{
      const stage  = STAGE_LABEL[ginfo.stage] || ginfo.stage;
      const suffix = GRADE_STAGE_SUFFIX[ginfo.grade] || "";
      const label  = suffix ? `【${{ginfo.grade}}級 · ${{stage}}${{suffix}}】` : `【${{ginfo.grade}}級】`;
      const vr     = ginfo.volRatio != null ? `量比${{ginfo.volRatio.toFixed(2)}}x` : "量比--";
      const bd     = ginfo.breadth  != null ? `廣度${{ginfo.breadth.toFixed(0)}}%`  : "";
      const parts  = [label, vr, bd].filter(Boolean).join(" ");
      infoEl.textContent = parts;

      const gradeColors = {{A:"#ff6680", B:"#fbbf24", C:"#c084fc", D:"#3d5470", E:"#00d26e"}};
      infoEl.style.color = gradeColors[ginfo.grade] || "#7a9bbb";
      infoEl.style.borderColor = (gradeColors[ginfo.grade] || "#7a9bbb") + "44";
    }}
  }});

  /* ── Stage 熱力格 + grade 標籤 ───────────────────────────────────────────── */
  document.querySelectorAll(".stage-heat-cell").forEach(cell => {{
    const group = cell.dataset.filter;
    const stat  = groupStats.get(group);
    const ginfo = groupGrades.get(group);
    if (!stat || !stat.count) return;

    const a = stat.sum / stat.count;
    cell.classList.remove("up", "down", "flat", "na");
    cell.classList.add(trend(a));
    cell.style.setProperty("--heat", heat(a));

    const ce = cell.querySelector(".stage-heat-change");
    const ve = cell.querySelector(".stage-heat-volume");
    const ge = cell.querySelector(".heat-grade");
    if (ce) ce.textContent = fmtPct(a);
    if (ve) ve.textContent = fmtInt(stat.volume) + " 張";
    if (ge && ginfo) {{
      ge.textContent = `[${{ginfo.grade}}]`;
      const gradeColors = {{A:"#ff6680", B:"#fbbf24", C:"#c084fc", D:"#3d5470", E:"#00d26e"}};
      ge.style.color = gradeColors[ginfo.grade] || "#7a9bbb";
    }}
  }});

  /* ── 輪動主控台 (Rotation Panel) ──────────────────────────────────────────── */
  const badgesEl = document.getElementById("rotationBadges");
  if (badgesEl) {{
    const signals = [];
    groupGrades.forEach((ginfo, group) => {{
      if (!"ABC".includes(ginfo.grade)) return;
      const suffix = GRADE_STAGE_SUFFIX[ginfo.grade] || "";
      const stage  = STAGE_LABEL[ginfo.stage] || ginfo.stage;
      const label  = suffix ? `${{ginfo.grade}}級 · ${{stage}}${{suffix}}` : `${{ginfo.grade}}級`;
      const vr     = ginfo.volRatio != null ? ` 量比${{ginfo.volRatio.toFixed(2)}}x` : "";
      const bd     = ginfo.breadth  != null ? ` 廣${{ginfo.breadth.toFixed(0)}}%`    : "";
      const shortName = group.split(" / ")[0];
      signals.push({{grade: ginfo.grade, html:
        `<span class="r-badge grade-${{ginfo.grade.toLowerCase()}}">` +
        `${{label}} ▸ ${{shortName}}` +
        `<span class="r-sub">${{vr}}${{bd}}</span></span>`
      }});
    }});

    signals.sort((x, y) => x.grade.localeCompare(y.grade));

    if (signals.length) {{
      badgesEl.innerHTML = signals.map(s => s.html).join("");
    }} else {{
      badgesEl.innerHTML = `<span class="r-empty" style="color:#3d5470">目前無 A/B/C 級訊號，盤面平靜或 D/E 狀態為主</span>`;
    }}
  }}

  /* ── 末升段警戒偵測 ─────────────────────────────────────────────────────── */
  const warnBox    = document.getElementById("warningBox");
  const warnDetail = document.getElementById("warningDetail");

  if (warnBox) {{
    let coreUpECount = 0;
    let laggardSignalCount = 0;
    let downstreamSignalCount = 0;

    groupGrades.forEach((ginfo, group) => {{
      if (CORE_UP.has(group)     && ginfo.grade === "E") coreUpECount++;
      if (LAGGARD_GRP.has(group) && "AC".includes(ginfo.grade)) laggardSignalCount++;
      if (DOWNSTREAM.has(group)  && "AC".includes(ginfo.grade)) downstreamSignalCount++;
    }});

    const triggerWarn = coreUpECount >= 1 && (laggardSignalCount >= 1 || downstreamSignalCount >= 2);

    if (triggerWarn) {{
      warnBox.style.display = "block";
      const detailParts = [];
      if (coreUpECount)           detailParts.push(`上游核心 ${{coreUpECount}} 族群 E 級退潮`);
      if (laggardSignalCount)     detailParts.push(`被動元件等低基期族群發動`);
      if (downstreamSignalCount)  detailParts.push(`${{downstreamSignalCount}} 個下游族群同步拉升`);
      if (warnDetail) warnDetail.textContent = detailParts.join(" ｜ ");
    }} else {{
      warnBox.style.display = "none";
    }}
  }}

  /* ── 成交量排行 ──────────────────────────────────────────────────────────── */
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
        page_title="台灣半導體 × AI 產業鏈｜智慧輪動儀表板",
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

    with st.spinner(f"正在批次抓取 {len(symbols)} 檔即時報價…"):
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
