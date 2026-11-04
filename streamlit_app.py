import streamlit as st

# Entry point: names the pages in the sidebar. Each tool lives in app_pages/.
st.navigation([
    st.Page('app_pages/handoff.py', title='月次引き継ぎ', icon='📋', default=True),
    st.Page('app_pages/schedule.py', title='日別ロット予定', icon='📅'),
]).run()
