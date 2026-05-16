# 台灣半導體 × AI 產業鏈熱力圖

## 資料夾結構

```
/ (repo root)
├── .gitignore
├── requirements.txt
├── streamlit_app.py               ← Streamlit 即時看板入口
├── .github/
│   └── workflows/
│       └── update_static_heatmap.yml  ← 定時更新靜態資料
└── docs/                          ← GitHub Pages 根目錄
    ├── build_semiconductor_ai_chain.py  ← 資料抓取 + HTML 生成腳本
    ├── index.html                 ← 自動生成，不要手動編輯
    └── representative_chain_data.json  ← 自動生成
```

> **重要**：`docs/index.html` 和 `docs/representative_chain_data.json`
> 是由 build 腳本**自動產生**的。請把它們 commit 進 repo，
> 讓 Streamlit Cloud 和 GitHub Pages 都能讀到初始資料。

---

## 兩種部署模式

### 1. GitHub Pages（純靜態，無即時報價）

- GitHub Actions 在台股交易日 09:00–13:00 每 15 分鐘執行一次 build 腳本
- Build 腳本從 TWSE / TPEX 公開 API 抓最新收盤價、月營收、EPS
- 結果寫入 `docs/index.html`，GitHub Pages 自動部署
- 設定：`Settings → Pages → Source: Deploy from branch → Branch: main → Folder: /docs`

### 2. Streamlit（即時報價）

- 讀取 `docs/index.html`（靜態快照）作為頁面骨架
- 透過 TWSE MIS API 每 20 秒抓即時報價（盤中）
- 用 JavaScript 把即時數字注入到靜態頁面裡
- 非交易時間會顯示警告，仍呈現靜態快照

---

## 首次設定步驟

### Step 1：本機生成初始靜態檔案

```bash
pip install xlrd
python docs/build_semiconductor_ai_chain.py
```

這會生成 `docs/index.html` 和 `docs/representative_chain_data.json`。

### Step 2：Commit 靜態檔案

```bash
git add docs/index.html docs/representative_chain_data.json
git commit -m "init: first static heatmap build"
git push
```

### Step 3：部署 Streamlit

在 [share.streamlit.io](https://share.streamlit.io) 連結此 repo，
主程式選 `streamlit_app.py`。

### Step 4：啟用 GitHub Actions

進入 repo 的 `Actions` 頁，解除封鎖（若為首次啟用）。
可先手動觸發一次 `Update Static Heatmap Data` 確認正常。

---

## 常見問題

| 問題 | 原因 | 解法 |
|------|------|------|
| Streamlit 顯示「找不到 index.html」 | 尚未執行 build 或檔案沒有 commit | 執行 Step 1–2 |
| 頁面只顯示上半部被截斷 | height 計算有誤 | `estimate_page_height()` 已動態計算，若還有問題請回報 |
| OTC 月報抓取失敗 | URL 自動偵測失敗 | `_find_otc_revenue_url()` 最多往前查 2 個月，通常自動解決 |
| 非交易時間即時報價為空 | TWSE MIS API 盤後無資料 | 正常，會顯示靜態快照並附上警告 |
