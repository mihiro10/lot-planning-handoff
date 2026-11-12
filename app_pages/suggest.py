"""計画提案: upload a 在庫表, get suggested 計画（倍） that keep stock within 最低〜最高 and
fit 米飯課 capacity (live AppSheet bookings by everyone else)."""
import datetime as dt
import json
import pathlib
import sys
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import requests
import streamlit as st

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import suggest_core as sc  # noqa: E402

st.set_page_config(page_title="計画提案", page_icon="🧮", layout="wide")
JST = ZoneInfo("Asia/Tokyo")
ROOT = pathlib.Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- AppSheet (read-only)

def _creds():
    try:
        return st.secrets["APPSHEET_APP_ID"], st.secrets["APPSHEET_ACCESS_KEY"]
    except Exception:
        env = ROOT / ".env"
        if env.exists():
            vals = dict(l.split("=", 1) for l in env.read_text().splitlines() if "=" in l and not l.startswith("#"))
            if "APPSHEET_APP_ID" in vals and "APPSHEET_ACCESS_KEY" in vals:
                return vals["APPSHEET_APP_ID"].strip(), vals["APPSHEET_ACCESS_KEY"].strip()
    return None, None


@st.cache_data(ttl=600, show_spinner="米飯課の依頼を取得中…")
def fetch_appsheet(start: dt.date, end: dt.date):
    app_id, key = _creds()
    if not app_id:
        return None, None, "AppSheet の接続設定がありません（他の担当者の依頼は考慮されません）"

    def find(table, selector=None):
        props = {"Locale": "ja-JP", "Timezone": "Tokyo Standard Time"}
        if selector:
            props["Selector"] = selector
        r = requests.post(f"https://api.appsheet.com/api/v2/apps/{app_id}/tables/{table}/Action",
                          headers={"ApplicationAccessKey": key},
                          json={"Action": "Find", "Properties": props, "Rows": []}, timeout=60)
        r.raise_for_status()
        return r.json() if r.text else []

    us = lambda d: d.strftime("%m/%d/%Y")
    try:
        reqs = find("Requests", f'Filter(Requests, AND([TargetDate] >= "{us(start)}", [TargetDate] <= "{us(end)}"))')
        prods = find("Products")
    except Exception as e:  # noqa: BLE001
        return None, None, f"AppSheet から取得できませんでした: {e}"
    return reqs, prods, None


# ---------------------------------------------------------------- inputs

st.title("🧮 計画提案")
st.caption("在庫表をアップロードすると、使用予測・最低／最高在庫・米飯課の空き時間をもとに、計画（倍）の案を出します。"
           "案は参考です。在庫表と AppSheet への入力はご自身で行ってください。")

up = st.file_uploader("在庫表（.xlsx）", type=["xlsx"])
if not up:
    st.info("例: 2026_8_2027_8早朝在庫表野末田村.xlsx")
    st.stop()

wb = sc.load_workbook(up.getvalue())
sheets = sc.planner_sheets(wb)
if not sheets:
    st.error("行種別・品目名の見出しがあるシートが見つかりません。")
    st.stop()

c1, c2, c3, c4 = st.columns([2, 1.4, 1.4, 1])
sheet = c1.selectbox("シート", sheets, index=next((i for i, s in enumerate(sheets) if "在庫" in s), 0))
all_items = sc.parse_sheet(wb[sheet])
planners = [p for p, _ in pd.Series([it.planner for it in all_items]).value_counts().items() if p and p != "担当"]
planner = c2.selectbox("計画担当", planners)
now = dt.datetime.now(JST)
base_day = c3.date_input("基準日（この日から計画）", value=now.date(),
                         min_value=all_items[0].days[1], max_value=all_items[0].days[-30])
horizon = c4.number_input("日数", 7, 28, 14)

o1, o2 = st.columns(2)
rice_only = o1.checkbox("米飯課の作業がある品目だけ計画する", value=True)
keep_confirmed = o2.checkbox("AppSheet で確定済みの依頼は動かさない", value=True)
with st.expander("米飯課の上限（分）"):
    k1, k2, k3, k4 = st.columns(4)
    caps = sc.Caps(target={"昼間": k1.number_input("昼間 目標", 0, 600, 180), "早朝": k3.number_input("早朝 目標", 0, 600, 120)},
                   hard={"昼間": k2.number_input("昼間 許容", 0, 600, 185), "早朝": k4.number_input("早朝 許容", 0, 600, 140)})

items_all = [it for it in all_items if it.planner == planner]
items = [it for it in items_all if it.plannable]
skipped = [it for it in items_all if not it.plannable]
days = items[0].days if items else []
if not items:
    st.warning("計画できる品目がありません（使用中・最低／最高在庫・ロットが必要）。")
    st.stop()

base = days.index(base_day)
# 4日前11時: before 11:00 today, base+4 can still change; after 11:00 it is locked too
first_free = base + sc.LOCK_DAYS + (1 if base_day == now.date() and now.hour >= 11 else 0)
end = min(first_free + int(horizon) - 1, len(days) - 15)
sc.prepare(items, base)

# ---------------------------------------------------------------- AppSheet + item settings

reqs, prods, err = fetch_appsheet(days[base], days[min(end + 5, len(days) - 1)])
if err:
    st.warning(err)
    reqs, prods = [], []
if prods:
    sc.match_products(items, prods)

st.subheader("品目の設定")
st.caption(f"変更できる最初の日: **{days[first_free]:%m/%d}**（4日前11時ルール）。それより前は今の計画のまま。"
           "いつもの倍・米飯課の作業は直せます。")
prod_names = ["（米飯課なし）"] + sorted({p["ProductName"] for p in prods or [] if p.get("TaskName") != "予定ブロック"})
cfg = pd.DataFrame([dict(品目名=it.name, ロット=f"{it.lot:g}{it.unit}", 最低=it.min, 最高=it.max,
                         リードタイム=it.lead, 平均出荷=round(it.extra["avg"]), いつもの倍=it.usual,
                         米飯課の作業=it.rice_product or "（米飯課なし）") for it in items])
cfg = st.data_editor(cfg, hide_index=True, use_container_width=True, disabled=["品目名", "ロット", "最低", "最高", "リードタイム", "平均出荷"],
                     column_config={"米飯課の作業": st.column_config.SelectboxColumn(options=prod_names),
                                    "いつもの倍": st.column_config.NumberColumn(min_value=0.1, step=0.1)})
for it, row in zip(items, cfg.to_dict("records")):
    it.usual = float(row["いつもの倍"] or 1)
    it.extra["step"] = it.usual if it.usual < 1 else 1.0
    it.rice_product = None if row["米飯課の作業"] == "（米飯課なし）" else row["米飯課の作業"]
sc.apply_products(items, prods or [])
if rice_only:
    skipped = skipped + [it for it in items if not it.rice_slot]
    items = [it for it in items if it.rice_slot]
    if not items:
        st.warning("米飯課の作業がある品目がありません。上の表で「米飯課の作業」を選ぶか、チェックを外してください。")
        st.stop()
if skipped:
    st.caption("対象外: " + "、".join(f"{it.name}（{'終了' if it.status != '使用中' else '最低/最高なし' if not it.plannable else '米飯課なし'}）"
                             for it in skipped))

others, mine_reqs = sc.requests_by_slot(reqs or [], planner, days[base], days[min(end + 5, len(days) - 1)])
own_locked = {}
for r in mine_reqs:
    if r["date"] < days[first_free]:
        own_locked[(r["date"], r["slot"])] = own_locked.get((r["date"], r["slot"]), 0) + r["minutes"]

fixed = sc.fixed_from_requests(items, mine_reqs, first_free) if keep_confirmed else {}
last_plan = max((x for it in items for x in range(first_free, end + 1) if it.plan[x]), default=first_free - 1)

# ---------------------------------------------------------------- plans

results = {}
cur_plans = {it.name: list(it.plan) for it in items}
summary, per_item, load = sc.score(items, cur_plans, base, first_free, end, others, caps, own_locked)
results["今の計画"] = dict(plans=cur_plans, summary=summary, per_item=per_item, load=load,
                       requests=sc.request_list(items, cur_plans, first_free, end, mine_reqs))
for mode, label in sc.MODES.items():
    plans = sc.plan_all(items, base, first_free, end, mode, others, own_locked, caps, fixed)
    summary, per_item, load = sc.score(items, plans, base, first_free, end, others, caps, own_locked)
    results[label] = dict(plans=plans, summary=summary, per_item=per_item, load=load,
                          requests=sc.request_list(items, plans, first_free, end, mine_reqs))

st.subheader(f"案の比較（{days[first_free]:%m/%d}〜{days[end]:%m/%d}・{len(items)}品目）")
comp = pd.DataFrame({k: v["summary"] for k, v in results.items()}).T
comp.columns = ["製造回数", "欠品見込み（品目×日）", "最低割れ（品目×日）", "最高超え（品目×日）", "平均在庫（最高比%）",
                "米飯課 許容超え（日・時間帯）", "米飯課 目標超え（日・時間帯）"]
st.dataframe(comp, use_container_width=True)
notes = ["見込みは使用予測ベース（使用予測が空の先の日は直近28日の平均）。米飯課の負荷には他の担当者の依頼と米飯課予定を含みます。"]
if last_plan < end:
    notes.append(f"今の計画は {days[last_plan]:%m/%d} まで入力されています。それより後の日は今の計画では製造なしとして数えています。")
st.caption("　".join(notes))

tabs = st.tabs(list(results.keys()))
for tab, (label, res) in zip(tabs, results.items()):
    with tab:
        # 品目 × 日 grid
        cols = [f"{days[x]:%m/%d}{'月火水木金土日'[days[x].weekday()]}" for x in range(first_free, end + 1)]
        trows = sc.plan_table(items, res["plans"], base, first_free, end)
        table = pd.DataFrame([[r["品目名"], r["行"]] + r["text"] for r in trows], columns=["品目名", "行"] + cols)
        marks = pd.DataFrame([["", ""] + r["mark"] for r in trows], columns=table.columns)
        css = {"diff": "background-color:#fff2b3", "low": "background-color:#f8d7da;color:#a4161a;font-weight:600",
               "neg": "background-color:#e5383b;color:white;font-weight:700", "high": "background-color:#d0e4f7;color:#1d4e89",
               "": ""}
        st.markdown("**計画（倍）と在庫推移（見込み）**　"
                    "<span style='background:#fff2b3;padding:0 4px'>今の計画と違う日</span> "
                    "<span style='background:#f8d7da;color:#a4161a;padding:0 4px'>最低割れ</span> "
                    "<span style='background:#e5383b;color:white;padding:0 4px'>欠品</span> "
                    "<span style='background:#d0e4f7;color:#1d4e89;padding:0 4px'>最高超え</span>",
                    unsafe_allow_html=True)
        st.dataframe(table.style.apply(lambda _: marks.map(css.get), axis=None),
                     hide_index=True, use_container_width=True, height=36 * (len(table) + 1) + 4,
                     column_config={"品目名": st.column_config.TextColumn(width="medium"),
                                    "行": st.column_config.TextColumn(width="small")})

        # 米飯課 load
        rows = []
        for x in range(first_free, end + 1):
            for slot in ("昼間", "早朝"):
                k = (days[x], slot)
                other = others.get(k, 0.0)
                total = res["load"].get(k, 0.0)
                rows += [dict(日=f"{days[x]:%m/%d}", 時間帯=slot, 区分="他の担当・米飯課予定", 分=other),
                         dict(日=f"{days[x]:%m/%d}", 時間帯=slot, 区分=planner, 分=max(total - other, 0))]
        ld = pd.DataFrame(rows)
        st.markdown("**米飯課の負荷（分）**")
        cc = st.columns(2)
        for c, slot in zip(cc, ("昼間", "早朝")):
            d = ld[ld["時間帯"] == slot]
            bars = alt.Chart(d).mark_bar().encode(
                x=alt.X("日:N", sort=None, title=None), y=alt.Y("分:Q", stack=True),
                color=alt.Color("区分:N", scale=alt.Scale(range=["#b0b7c3", "#2f6fba"]), legend=alt.Legend(orient="bottom", title=None)))
            rule = alt.Chart(pd.DataFrame({"分": [caps.target[slot]]})).mark_rule(color="#d9534f", strokeDash=[4, 3]).encode(y="分:Q")
            c.markdown(f"{slot}（目標 {caps.target[slot]}分・許容 {caps.hard[slot]}分）")
            c.altair_chart((bars + rule).properties(height=240), use_container_width=True)

        # requests
        st.markdown("**AppSheet への依頼（米飯課の作業がある品目）**")
        rq = pd.DataFrame(res["requests"])
        if rq.empty:
            st.caption("依頼なし")
        else:
            rq["製造日"] = pd.to_datetime(rq["製造日"]).dt.strftime("%m/%d")
            st.dataframe(rq, hide_index=True, use_container_width=True)

        # per item + stock chart
        with st.expander("品目ごとの見込み"):
            st.dataframe(pd.DataFrame(res["per_item"]), hide_index=True, use_container_width=True)
            pick = st.selectbox("在庫の推移を見る品目", [it.name for it in items], key=f"pick-{label}")
            it = next(i for i in items if i.name == pick)
            ss = sc.stock_series(it, res["plans"][it.name], base, end, first_free)
            cs = sc.stock_series(it, it.plan, base, end, first_free)
            sd = pd.DataFrame([dict(日=days[x], 在庫=ss[x], 案=label) for x in range(base, end + 1)] +
                              [dict(日=days[x], 在庫=cs[x], 案="今の計画") for x in range(base, end + 1)])
            band = alt.Chart(pd.DataFrame({"min": [it.min], "max": [it.max]})).mark_rect(opacity=0.12, color="#2f9e44").encode(y="min:Q", y2="max:Q")
            line = alt.Chart(sd).mark_line(point=True).encode(x=alt.X("日:T", title=None), y=alt.Y("在庫:Q"), color=alt.Color("案:N", legend=alt.Legend(orient="bottom", title=None)))
            st.altair_chart((band + line).properties(height=240), use_container_width=True)
            st.caption("緑の帯 = 最低〜最高在庫")

xlsx = sc.export_excel(items, {k: v for k, v in results.items()}, base, first_free, end, f"{planner} 計画提案")
st.download_button("Excel をダウンロード（印刷用・全案）", xlsx,
                   file_name=f"計画提案_{planner}_{days[first_free]:%m%d}-{days[end]:%m%d}.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
