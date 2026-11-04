from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

# The tool is a self-contained page (parsing, print and Excel export all run in the browser),
# so it's embedded as-is; uploaded workbooks never reach the Streamlit server.
st.set_page_config(page_title='日別ロット予定', page_icon='📅', layout='wide')
html = (Path(__file__).parent.parent / 'schedule_by_date' / 'lot_schedule_tool.html').read_text(encoding='utf-8')
components.html(html, height=1600, scrolling=True)
