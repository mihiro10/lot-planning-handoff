import streamlit as st

# Entry point: names the pages in the sidebar. Each tool lives in app_pages/.
st.navigation([
    st.Page('app_pages/schedule.py', title='日別ロット予定', icon='📅', default=True),
]).run()
