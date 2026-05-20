"""
暫時用這個檔案取代 streamlit_app.py 測試 yfinance 能不能在 Streamlit Cloud 跑。
測完之後再換回去。
"""
import streamlit as st
import yfinance as yf
 
st.title("yfinance 測試")
 
codes = ["2330", "2454", "2317", "2382", "3711"]
 
for code in codes:
    ticker = f"{code}.TW"
    try:
        tk = yf.Ticker(ticker)
        price = tk.fast_info.last_price
        prev  = tk.fast_info.previous_close
        chg   = (price / prev - 1) * 100 if price and prev else None
        st.success(f"{ticker}: {price:.2f}　漲跌 {chg:+.2f}%")
    except Exception as e:
        st.error(f"{ticker}: 失敗 → {e}")
