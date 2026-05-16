# Heatmap

台股半導體 / AI 盤中熱力圖專案。

這份專案現在只以兩個地方為主：

1. `GitHub`
2. `Streamlit Community Cloud`

也就是：

- `GitHub` 負責放原始碼
- `Streamlit Community Cloud` 負責執行網頁

不需要把 API key 寫進 repo，也不建議把 API key commit 到 GitHub。

## 專案結構

```text
Heatmap/
├─ .github/workflows/update_representative_chain.yml
├─ .streamlit/secrets.toml.example
├─ docs/
│  ├─ build_semiconductor_ai_chain.py
│  ├─ GITHUB_DEPLOY_GUIDE.md
│  ├─ STREAMLIT_DEPLOY_GUIDE.md
│  ├─ index.html
│  └─ representative_chain_data.json
├─ requirements.txt
└─ streamlit_app.py
```

## 入口檔

- Streamlit 主程式：`streamlit_app.py`
- 靜態 HTML 頁：`docs/index.html`

## 即時資料來源

目前 Streamlit 版設計為接 `Fugle`：

1. `Intraday Quote`
   - 逐檔即時報價
   - 適合代表股精簡版
2. `Snapshot Quotes`
   - 市場快照
   - 適合更完整熱力圖
   - 通常需要較高方案

## API key 放哪裡

如果你部署到 `Streamlit Community Cloud`：

- 不要把 key 放進 GitHub repo
- 直接放在 `App Settings -> Secrets`

格式：

```toml
[fugle]
api_key = "你的 Fugle API Key"
use_snapshot = false
```

完整流程看：

- `docs/STREAMLIT_DEPLOY_GUIDE.md`
