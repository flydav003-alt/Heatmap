# GitHub 部署說明

這份專案現在建議你把 `Heatmap` 當成一個獨立 repo 來用。

也就是說：

- repo root = `Heatmap/`
- GitHub Pages 首頁 = `docs/index.html`
- Streamlit 首頁 = `streamlit_app.py`

## 一、資料夾結構

```text
Heatmap/
├─ .github/workflows/update_representative_chain.yml
├─ .streamlit/secrets.toml.example
├─ docs/
│  ├─ index.html
│  ├─ representative_chain_data.json
│  ├─ build_semiconductor_ai_chain.py
│  ├─ GITHUB_DEPLOY_GUIDE.md
│  └─ STREAMLIT_DEPLOY_GUIDE.md
├─ requirements.txt
├─ README.md
└─ streamlit_app.py
```

## 二、GitHub Pages 靜態版

適合：

- 盤後資料
- GitHub Actions 定時更新
- 不接即時 API

### 設定方式

1. 把 `Heatmap` 內容推到一個新的 GitHub repo
2. 到 `Settings -> Pages`
3. `Source` 選 `Deploy from a branch`
4. Branch 選 `main`
5. Folder 選 `/docs`
6. 存檔

之後首頁就是：

- `docs/index.html`

## 三、GitHub Actions 更新靜態資料

workflow 檔案在：

- `.github/workflows/update_representative_chain.yml`

它會做的事：

1. 安裝 Python
2. 安裝 `xlrd`
3. 執行 `docs/build_semiconductor_ai_chain.py`
4. 更新：
   - `docs/index.html`
   - `docs/representative_chain_data.json`

### 手動更新

1. 到 GitHub repo 的 `Actions`
2. 點 `Update Representative Chain Data`
3. 點 `Run workflow`

### 排程更新

目前設定是：

- 台灣交易時段內，每 15 分鐘跑一次 workflow

但要注意：

- 這種方式適合盤後或半即時整理
- 不適合真的盤中秒級即時資料

## 四、Fugle API Key 放哪裡

### 如果你要給 GitHub Actions 用

到：

- `Settings -> Secrets and variables -> Actions`

新增：

- `FUGLE_API_KEY`

如果之後你有修改 workflow 要用到 Fugle，就在 workflow 裡加：

```yaml
env:
  FUGLE_API_KEY: ${{ secrets.FUGLE_API_KEY }}
```

## 五、為什麼 GitHub Pages 不能直接即時抓

因為 GitHub Pages 是靜態頁：

- 不能安全藏 API key
- 不能在使用者按 `F5` 時執行 Python
- 不能直接當後端代理

所以：

- 要 `純 GitHub`：用 GitHub Actions 更新靜態資料
- 要 `按按鈕就抓最新`：用 Streamlit 或其他後端
