from __future__ import annotations

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components


ROOT = Path(__file__).resolve().parent
HTML_PATH = ROOT / "docs" / "index.html"


def main() -> None:
    st.set_page_config(
        page_title="台灣半導體 × AI 產業鏈全圖",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    html_content = HTML_PATH.read_text(encoding="utf-8")

    st.markdown(
        """
        <style>
          .block-container {
            padding-top: 0.2rem;
            padding-bottom: 0;
            max-width: 100%;
          }
          [data-testid="stHeader"] {
            background: transparent;
          }
          [data-testid="stSidebar"] {
            display: none;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )

    components.html(html_content, height=4200, scrolling=True)


if __name__ == "__main__":
    main()
