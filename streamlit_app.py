import streamlit as st
from io import BytesIO
from datetime import datetime, timedelta
import traceback
import openpyxl

COL_CODE   = 5
COL_NAME   = 10
COL_H      = 7   # 棚卸し前在庫
COL_RTYPE  = 19  # 行種別
COL_DAY1   = 21
TRANSFER_TYPES = ['備考', '計画（倍）', '使用予測']


def parse_date(val):
    if isinstance(val, datetime):
        return val.date()
    return None


def build_product_map(ws):
    products = {}
    current_code = None
    for row in range(3, ws.max_row + 1):
        rtype = ws.cell(row, COL_RTYPE).value
        code  = ws.cell(row, COL_CODE).value
        if not rtype:
            continue
        if code:
            current_code = code
        if current_code:
            if current_code not in products:
                products[current_code] = {'rows': {}, 'name': None}
            products[current_code]['rows'][rtype] = row
            if rtype == '備考' and code:
                products[current_code]['name'] = ws.cell(row, COL_NAME).value
    return products


def run_handoff(may_bytes, jun_bytes):
    result = {
        'success': False, 'error': None, 'detail': None,
        'may_start': None, 'jun_start': None, 'may_last_date': None,
        'transferred': [], 'new_products': [], 'discontinued': [], 'warnings': [],
        'jun_bytes': None,
    }
    try:
        may_wb = openpyxl.load_workbook(BytesIO(may_bytes), data_only=True)
        jun_wb = openpyxl.load_workbook(BytesIO(jun_bytes))
        may_ws = may_wb.active
        jun_ws = jun_wb.active

        may_start = parse_date(may_ws.cell(2, COL_DAY1).value)
        jun_start = parse_date(jun_ws.cell(2, COL_DAY1).value)

        if not may_start:
            result['error'] = '先月ファイルのU2セルに日付が見つかりません。日付形式を確認してください。'
            return result
        if not jun_start:
            result['error'] = '今月ファイルのU2セルに日付が見つかりません。日付形式を確認してください。'
            return result
        if jun_start <= may_start:
            result['error'] = f'今月の開始日（{jun_start}）が先月の開始日（{may_start}）より前です。ファイルの順番を確認してください。'
            return result

        result['may_start']     = str(may_start)
        result['jun_start']     = str(jun_start)
        may_last_date           = jun_start - timedelta(days=1)
        result['may_last_date'] = str(may_last_date)

        may_last_col      = COL_DAY1 + (may_last_date - may_start).days
        overlap_start_col = COL_DAY1 + (jun_start     - may_start).days

        if overlap_start_col > may_ws.max_column:
            result['error'] = f'先月ファイルにオーバーフロー列（{jun_start}以降）が見つかりません。先月ファイルに翌月分の列が含まれているか確認してください。'
            return result

        overlap_days = min(
            may_ws.max_column - overlap_start_col + 1,
            jun_ws.max_column - COL_DAY1 + 1,
        )

        may_products = build_product_map(may_ws)
        jun_products = build_product_map(jun_ws)

        if not may_products:
            result['error'] = '先月ファイルにコード（F列）が入力された商品が見つかりません。'
            return result
        if not jun_products:
            result['error'] = '今月ファイルにコード（F列）が入力された商品が見つかりません。'
            return result

        for code, info in may_products.items():
            if code not in jun_products:
                result['discontinued'].append({'code': code, 'name': info['name'] or '（名前なし）'})

        for code, jun_info in jun_products.items():
            name = jun_info['name'] or '（名前なし）'
            if code not in may_products:
                result['new_products'].append({'code': code, 'name': name})
                continue

            may_rows = may_products[code]['rows']
            jun_rows = jun_info['rows']
            item = {
                'code': code, 'name': name,
                'takadoshi_mae': None, 'takadoshi_warning': False,
                'transferred_types': [], 'skipped_types': [],
            }

            if '最終' not in may_rows:
                result['warnings'].append(f'[{code}] {name}: 先月に最終行が見つかりません。棚卸し前在庫をスキップしました。')
            elif '備考' not in jun_rows:
                result['warnings'].append(f'[{code}] {name}: 今月に備考行が見つかりません。棚卸し前在庫をスキップしました。')
            else:
                val = may_ws.cell(may_rows['最終'], may_last_col).value
                jun_ws.cell(jun_rows['備考'], COL_H).value = val
                item['takadoshi_mae'] = val
                if val is None:
                    item['takadoshi_warning'] = True
                    result['warnings'].append(f'[{code}] {name}: 先月末（{may_last_date}）の最終在庫が空白です。手動で確認してください。')

            for rtype in TRANSFER_TYPES:
                if rtype not in may_rows:
                    item['skipped_types'].append(f'{rtype}（先月に行なし）')
                    continue
                if rtype not in jun_rows:
                    item['skipped_types'].append(f'{rtype}（今月に行なし）')
                    continue
                any_val = False
                for i in range(overlap_days):
                    val = may_ws.cell(may_rows[rtype], overlap_start_col + i).value
                    jun_ws.cell(jun_rows[rtype], COL_DAY1 + i).value = val
                    if val is not None:
                        any_val = True
                item['transferred_types'].append(rtype)
                if not any_val:
                    result['warnings'].append(f'[{code}] {name} / {rtype}: 転記しましたが先月のオーバーフロー欄がすべて空白でした。')

            result['transferred'].append(item)

        out = BytesIO()
        jun_wb.save(out)
        out.seek(0)
        result['jun_bytes'] = out.getvalue()
        result['success'] = True

    except Exception as e:
        result['error'] = f'予期しないエラーが発生しました: {e}'
        result['detail'] = traceback.format_exc()

    return result


# ── UI ──────────────────────────────────────────────────────────────────────

st.set_page_config(page_title='月次引き継ぎ', page_icon='📋', layout='centered')
st.title('月次引き継ぎ')
st.caption('今月のオーバーフロー計画（備考・計画・入庫・使用予測）と今月末在庫を来月ファイルに転記します。')

col1, col2 = st.columns(2)
with col1:
    may_file = st.file_uploader('今月のファイル（記入済み）（例: 6月計画.xlsx）', type='xlsx', key='may')
with col2:
    jun_file = st.file_uploader('来月のファイル（空白）（例: 7月計画.xlsx）', type='xlsx', key='jun')

if st.button('引き継ぎを実行', type='primary', disabled=not (may_file and jun_file)):
    with st.spinner('処理中...'):
        result = run_handoff(may_file.read(), jun_file.read())

    if result['error']:
        st.error(result['error'])
        if result.get('detail'):
            with st.expander('詳細エラー'):
                st.code(result['detail'])
        st.stop()

    # Summary bar
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric('今月末', result['may_last_date'])
    c2.metric('来月開始', result['jun_start'])
    c3.metric('転記済み', len(result['transferred']))
    c4.metric('新規', len(result['new_products']))
    c5.metric('廃止', len(result['discontinued']))

    # Download
    st.download_button(
        '📥 来月のファイルをダウンロード（記入済み）',
        data=result['jun_bytes'],
        file_name=jun_file.name,
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        type='primary',
    )

    # Warnings
    if result['warnings']:
        with st.expander(f'⚠️ 確認が必要な項目 ({len(result["warnings"])}件)', expanded=True):
            for w in result['warnings']:
                st.warning(w, icon='⚠️')

    # Transferred
    if result['transferred']:
        st.subheader('転記済み商品')
        for item in result['transferred']:
            takadoshi = (
                f"{item['takadoshi_mae']} ⚠️" if item['takadoshi_warning']
                else str(item['takadoshi_mae']) if item['takadoshi_mae'] is not None
                else '空白'
            )
            transferred = '　'.join(item['transferred_types']) or 'なし'
            skipped     = '　'.join(item['skipped_types'])     or 'なし'
            with st.expander(f"✅ [{item['code']}] {item['name']}"):
                st.write(f"**棚卸し前在庫:** {takadoshi}")
                st.write(f"**転記行:** {transferred}")
                if item['skipped_types']:
                    st.write(f"**スキップ:** {skipped}")

    # New products
    if result['new_products']:
        st.subheader('新規商品（在庫数を手動入力してください）')
        for p in result['new_products']:
            st.warning(f"[{p['code']}] {p['name']}", icon='🆕')

    # Discontinued
    if result['discontinued']:
        st.subheader('廃止商品（来月ファイルに存在しないためスキップ）')
        for p in result['discontinued']:
            st.info(f"[{p['code']}] {p['name']}", icon='🗂️')
