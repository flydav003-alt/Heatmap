# GitHub 最終上線目錄清單

這份就是 `Heatmap` 資料夾整理完後，適合直接推上 GitHub 的版本。

## 要上傳到 GitHub 的檔案

```text
Heatmap/
├─ .github/
│  └─ workflows/
│     └─ update_representative_chain.yml
├─ .streamlit/
│  └─ secrets.toml.example
├─ docs/
│  ├─ build_semiconductor_ai_chain.py
│  ├─ GITHUB_DEPLOY_GUIDE.md
│  ├─ STREAMLIT_DEPLOY_GUIDE.md
│  ├─ index.html
│  └─ representative_chain_data.json
├─ .gitignore
├─ GITHUB_UPLOAD_CHECKLIST.md
├─ README.md
├─ requirements.txt
└─ streamlit_app.py
```

## 不要上傳到 GitHub 的內容

這些是本機依賴、快取或 secrets：

```text
Heatmap/.vendor_py/
Heatmap/__pycache__/
Heatmap/docs/.cache_sem_ai/
Heatmap/.streamlit/secrets.toml
```

## GitHub repo 根目錄應該長怎樣

也就是說，你推上 GitHub 之後，repo root 要直接看到：

- `.github/`
- `.streamlit/`
- `docs/`
- `.gitignore`
- `README.md`
- `requirements.txt`
- `streamlit_app.py`

## Streamlit Community Cloud 入口檔

部署時主檔填：

```text
streamlit_app.py
```

## GitHub Pages 靜態入口

如果你還要保留靜態頁，Pages 首頁是：

```text
docs/index.html
```

## Fugle API key 放哪裡

不要放 GitHub repo。

請放在：

- `Streamlit Community Cloud -> App Settings -> Secrets`

內容：

```toml
[fugle]
api_key = "你的 Fugle API Key"
use_snapshot = false
```
