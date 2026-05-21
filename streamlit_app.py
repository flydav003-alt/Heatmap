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


# ── 整合報價 ───────────────────────────────────────────────────────────────────
@st.cache_data(ttl=180, show_spinner=False)  # cache 3分鐘，避免重複打 Yahoo
def fetch_all_quotes(symbols: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    quotes: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for code, exchange in symbols:
        try:
            suffix = ".TWO" if exchange == "otc" else ".TW"
            now    = datetime.now()
            fi     = yf.Ticker(f"{code}{suffix}").fast_info
            price  = fi.last_price
            prev   = fi.previous_close
            if price is None or price == 0:
                fallback = ".TW" if suffix == ".TWO" else ".TWO"
                fi    = yf.Ticker(f"{code}{fallback}").fast_info
                price = fi.last_price
                prev  = fi.previous_close
            if price is None or price == 0:
                continue
            chg_pct  = (price / prev - 1) * 100 if prev else None
            vol      = getattr(fi, "last_volume", None)
            vol_lots = vol / 1000 if vol else None
            quotes[code] = {
                "price":       float(price),
                "change_pct":  float(chg_pct) if chg_pct is not None else None,
                "volume_lots": float(vol_lots) if vol_lots is not None else None,
                "time":        now.strftime("%H:%M:%S"),
                "date":        now.strftime("%Y-%m-%d"),
            }
        except Exception as exc:
            errors.append(f"{code}: {exc}")
        time.sleep(0.05)

    latest_time = "--:--:--"
    latest_date = "--"
    for q in quotes.values():
        if q.get("time"):
            latest_time = q["time"]
        if q.get("date"):
            latest_date = q["date"]

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

# Groups considered "core upstream" for exit-signal detection
CORE_UPSTREAM = {"IC設計 / IP / ASIC", "晶圓代工 / 功率半導體"}
# Groups considered "passive/laggard" for exit-signal detection
LAGGARD_GROUPS = {"被動元件"}
# Groups considered "downstream" for exit-signal detection
DOWNSTREAM_GROUPS = {"AI伺服器 / 機櫃組裝", "網通 / 光通訊 / CPO"}


def inject_live_script(base_html: str, payload: dict[str, Any]) -> str:
    live_json = json.dumps(payload, ensure_ascii=False)
    stage_map_json = json.dumps(GROUP_STAGE_MAP, ensure_ascii=False)
    core_upstream_json = json.dumps(list(CORE_UPSTREAM), ensure_ascii=False)
    laggard_json = json.dumps(list(LAGGARD_GROUPS), ensure_ascii=False)
    downstream_json = json.dumps(list(DOWNSTREAM_GROUPS), ensure_ascii=False)

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
    return isFinite(n) ? n : null;
  }};

  /* ── 主迴圈：掃描所有個股行，計算各族群指標 ─────────────────────────────── */
  // groupStats: 除了基本的 sum/count/volume 之外，
  // 現在增加追蹤 upCount（上漲家數）與歷史資料累計
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

    // 讀取預埋的歷史 dataset 屬性（build 時已寫入）
    // 若屬性缺失或為空，safe() 會回傳 null，不引發 JS 錯誤
    const avgVol20 = safe(row.dataset.avgVol20);  // dataset attr: data-avg-vol20
    const p5close  = safe(row.dataset.p5Close);   // dataset attr: data-p5-close
    const p20high  = safe(row.dataset.p20High);   // dataset attr: data-p20-high

    // 累計族群統計
    const group = row.dataset.group || "";
    if (!groupStats.has(group)) {{
      groupStats.set(group, {{
        sum: 0, count: 0, volume: 0,
        upCount: 0,               // 上漲家數
        totalCount: 0,            // 族群總家數（有報價）
        sumAvgVol20: 0, cntVol20: 0,   // 20日均量加總
        sumPrice: 0, cntPrice: 0,       // 即時均價
        sumP5close: 0, cntP5: 0,        // 5日前收盤均價
        sumP20high: 0, cntP20: 0,       // 20日最高均價
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

    // 歷史指標累計（只在有效時累計，否則跳過）
    if (avgVol20 != null && avgVol20 > 0) {{ stat.sumAvgVol20 += avgVol20; stat.cntVol20++; }}
    if (price    != null) {{ stat.sumPrice   += price;   stat.cntPrice++; }}
    if (p5close  != null) {{ stat.sumP5close += p5close; stat.cntP5++; }}
    if (p20high  != null) {{ stat.sumP20high += p20high; stat.cntP20++; }}
  }});

  /* ── 計算族群四大指標 + 輪動等級 ─────────────────────────────────────────── */
  /*
   * 指標定義：
   * breadth    = upCount / totalCount * 100  (廣度 %)
   * volRatio   = stat.volume / (sumAvgVol20 * cntVol20 / cntVol20)
   *            = 今日即時總量 / 族群20日預估總均量
   * mom5d      = (avgPrice / avgP5close - 1) * 100  (5日強弱度 %)
   * drawdown   = (avgPrice / avgP20high - 1) * 100  (距20日高點回檔 %)，負值 = 回檔
   *
   * 輪動等級：
   * A: avgChange > 1.2% AND breadth >= 65% AND volRatio >= 1.35
   * B: avgChange > 0.6% AND breadth >= 50% AND volRatio >= 1.15
   * C: volRatio >= 1.35 AND drawdown < -10% AND mom5d 小 (mom5d < 3%)
   * E: avgChange < -1.0% OR breadth < 25%
   * D: 其餘
   */

  const groupGrades = new Map();  // group -> {{grade, stage, metrics}}

  groupStats.forEach((stat, group) => {{
    if (!stat.count) return;

    const avgChange = stat.sum / stat.count;
    const breadth   = stat.totalCount > 0 ? (stat.upCount / stat.totalCount) * 100 : null;
    const avgVol20  = stat.cntVol20  > 0 ? stat.sumAvgVol20 / stat.cntVol20 : null;
    // volRatio: 今日即時量(張) vs 族群 20日均量(張)
    // stat.volume 已是張; avgVol20 也是張；族群預估總均量 = avgVol20 * cntPrice（人數）
    const estTotalVol20 = avgVol20 != null ? avgVol20 * stat.cntVol20 : null;
    const volRatio = (estTotalVol20 != null && estTotalVol20 > 0)
      ? stat.volume / estTotalVol20 : null;

    const avgPrice   = stat.cntPrice > 0 ? stat.sumPrice   / stat.cntPrice  : null;
    const avgP5close = stat.cntP5    > 0 ? stat.sumP5close / stat.cntP5     : null;
    const avgP20high = stat.cntP20   > 0 ? stat.sumP20high / stat.cntP20    : null;

    const mom5d    = (avgPrice != null && avgP5close != null && avgP5close > 0)
      ? (avgPrice / avgP5close - 1) * 100 : null;
    const drawdown = (avgPrice != null && avgP20high != null && avgP20high > 0)
      ? (avgPrice / avgP20high - 1) * 100 : null;

    // 等級判定
    let grade = "D";
    if (
      avgChange < -1.0 ||
      (breadth != null && breadth < 25)
    ) {{
      grade = "E";
    }} else if (
      avgChange > 1.2 &&
      (breadth == null || breadth >= 65) &&
      (volRatio == null || volRatio >= 1.35)
    ) {{
      grade = "A";
    }} else if (
      avgChange > 0.6 &&
      (breadth == null || breadth >= 50) &&
      (volRatio == null || volRatio >= 1.15)
    ) {{
      grade = "B";
    }} else if (
      (volRatio != null && volRatio >= 1.35) &&
      (drawdown != null && drawdown < -10) &&
      (mom5d    != null && mom5d    < 3)
    ) {{
      grade = "C";
    }}

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
  // stage 顯示名稱對應
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

    // rotation info 行
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
      const vr     = ginfo.volRatio != null ? `量比${{ginfo.volRatio.toFixed(2)}}x` : "";
      const bd     = ginfo.breadth  != null ? `廣度${{ginfo.breadth.toFixed(0)}}%`  : "";
      const parts  = [label, vr, bd].filter(Boolean).join(" ");
      infoEl.textContent = parts;

      // 根據等級給顏色
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

      // 族群名截短顯示
      const shortName = group.split(" / ")[0];
      signals.push({{grade: ginfo.grade, html:
        `<span class="r-badge grade-${{ginfo.grade.toLowerCase()}}">` +
        `${{label}} ▸ ${{shortName}}` +
        `<span class="r-sub">${{vr}}${{bd}}</span></span>`
      }});
    }});

    // 依照 A > B > C 排序
    signals.sort((x, y) => x.grade.localeCompare(y.grade));

    if (signals.length) {{
      badgesEl.innerHTML = signals.map(s => s.html).join("");
    }} else {{
      badgesEl.innerHTML = `<span class="r-empty" style="color:#3d5470">目前無 A/B/C 級訊號，盤面平靜或 D/E 狀態為主</span>`;
    }}
  }}

  /* ── 末升段警戒偵測 ─────────────────────────────────────────────────────── */
  /*
   * 觸發條件：
   *  - 上游核心（IC設計/晶圓代工）出現 E 級退潮
   *  - 同時 被動元件 或 多個下游族群 出現 C 級或 A 級
   */
  const warnBox   = document.getElementById("warningBox");
  const warnDetail = document.getElementById("warningDetail");

  if (warnBox) {{
    let coreUpECount = 0;
    let laggardSignalCount = 0;
    let downstreamSignalCount = 0;

    groupGrades.forEach((ginfo, group) => {{
      if (CORE_UP.has(group)      && ginfo.grade === "E") coreUpECount++;
      if (LAGGARD_GRP.has(group)  && "AC".includes(ginfo.grade)) laggardSignalCount++;
      if (DOWNSTREAM.has(group)   && "AC".includes(ginfo.grade)) downstreamSignalCount++;
    }});

    const triggerWarn = coreUpECount >= 1 && (laggardSignalCount >= 1 || downstreamSignalCount >= 2);

    if (triggerWarn) {{
      warnBox.style.display = "block";

      // 組合警示細節
      const detailParts = [];
      if (coreUpECount)        detailParts.push(`上游核心 ${{coreUpECount}} 族群 E 級退潮`);
      if (laggardSignalCount)  detailParts.push(`被動元件等低基期族群發動`);
      if (downstreamSignalCount > 0) detailParts.push(`${{downstreamSignalCount}} 個下游族群同步拉升`);
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
