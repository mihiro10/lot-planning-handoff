import streamlit as st

# Entry point: names the pages in the sidebar. Each tool lives in app_pages/.
st.navigation([
    st.Page('app_pages/schedule.py', title='日別ロット予定', icon='📅', default=True),
    st.Page('app_pages/suggest.py', title='計画提案', icon='🧮'),
]).run()
