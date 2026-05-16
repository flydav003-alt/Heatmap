# Streamlit Community Cloud 完整部署流程

這份流程只講你要的兩個地方：

1. `GitHub`
2. `Streamlit Community Cloud`

## 一、先準備 GitHub repo

### 1. 建立 repo

1. 到 GitHub
2. 點 `New repository`
3. 建一個新 repo
   - 建議名稱：`Heatmap`
4. 建立完成後，把這個資料夾內的所有內容上傳到 repo 根目錄

重點：

- `streamlit_app.py` 要在 repo root
- `requirements.txt` 要在 repo root
- `docs/` 保留在 repo 內

## 二、確認不要把 API key 放進 repo

### 不要上傳這個檔案

- `.streamlit/secrets.toml`

repo 裡只保留：

- `.streamlit/secrets.toml.example`

這樣才不會把真正的 key commit 上去。

## 三、到 Streamlit Community Cloud 部署

官方入口：

- [Streamlit Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud)

### 1. 登入

1. 到 Streamlit Community Cloud
2. 用 GitHub 帳號登入
3. 授權 Streamlit 讀取你的 GitHub repo

### 2. 建立 App

1. 在 Workspace 右上角點 `Create app`
2. 選擇你的 GitHub repo
3. Branch 選 `main`
4. Main file path 填：

```text
streamlit_app.py
```

## 四、在 Streamlit 填 Fugle API key

在建立 App 的 `Advanced settings`，或 App 建好後的 `Settings -> Secrets`，
貼上這段：

```toml
[fugle]
api_key = "你的 Fugle API Key"
use_snapshot = false
```

### 欄位說明

- `api_key`
  - 你的 Fugle key
- `use_snapshot = false`
  - 逐檔抓代表股即時資料
  - 最適合你現在這種精簡版熱力圖
- `use_snapshot = true`
  - 走市場快照
  - 較適合更完整熱力圖
  - 通常需要 Fugle 較高方案

## 五、部署完成後怎麼更新

之後你只要：

1. 改 GitHub repo 裡的程式
2. push 到 `main`

Streamlit Community Cloud 會重新部署。

如果你只是打開網頁、按重新整理、按 `F5`：

- 頁面會重新執行 `streamlit_app.py`
- 如果有設定 Fugle secrets，就會重新抓最新資料

## 六、如果沒有填 API key 會怎樣

沒有設定 Fugle secrets 時：

- Streamlit 版會退回顯示靜態資料
- 不會壞掉
- 只是沒有盤中即時刷新

## 七、你最需要記住的兩個檔案

1. GitHub repo 根目錄：

- `streamlit_app.py`

2. Streamlit Secrets 內容：

```toml
[fugle]
api_key = "你的 Fugle API Key"
use_snapshot = false
```
