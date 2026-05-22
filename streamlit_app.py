import streamlit as st
from io import BytesIO
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import traceback
import openpyxl

COL_CODE   = 6
COL_NAME   = 11
COL_H      = 8
COL_RTYPE  = 20
COL_DAY1   = 21
TRANSFER_TYPES  = ['備考', '計画（倍）', '使用予測']
FUZZY_THRESHOLD = 0.82


def parse_date(val):
    if isinstance(val, datetime):
        return val.date()
    return None


def build_product_map(ws):
    """品目名をキーに {行種別: 行番号} のマップを作成。備考行から順にスキャンして同じブロックの全行を収集する。"""
    products = {}
    current_name = None
    for row in range(3, ws.max_row + 1):
        rtype = ws.cell(row, COL_RTYPE).value
        if not rtype:
            continue
        if rtype == '備考':
            name = ws.cell(row, COL_NAME).value
            if name:
                current_name = name
                products[name] = {'rows': {}, 'code': ws.cell(row, COL_CODE).value}
            else:
                current_name = None
        if current_name:
            products[current_name]['rows'][rtype] = row
    return products


def _similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()


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

        may_products = build_product_map(may_ws)
        jun_products = build_product_map(jun_ws)

        if not may_products:
            result['error'] = '先月ファイルに品目名（K列）が入力された商品が見つかりません。'
            return result
        if not jun_products:
            result['error'] = '今月ファイルに品目名（K列）が入力された商品が見つかりません。'
            return result

        for name, info in may_products.items():
            if name not in jun_products:
                result['discontinued'].append({'name': name, 'code': info['code']})

        for name, jun_info in jun_products.items():
            code = jun_info['code']
            if name not in may_products:
                result['new_products'].append({'name': name, 'code': code})
                continue

            may_rows = may_products[name]['rows']
            jun_rows = jun_info['rows']
            item = {
                'name': name, 'code': code,
                'takadoshi_mae': None, 'takadoshi_warning': False,
                'transferred_types': [], 'skipped_types': [],
            }

            if '最終' not in may_rows:
                result['warnings'].append(f'{name}: 先月に最終行が見つかりません。棚卸し前在庫をスキップしました。')
            elif '備考' not in jun_rows:
                result['warnings'].append(f'{name}: 今月に備考行が見つかりません。棚卸し前在庫をスキップしました。')
            else:
                val = may_ws.cell(may_rows['最終'], may_last_col).value
                jun_ws.cell(jun_rows['備考'], COL_H).value = val
                item['takadoshi_mae'] = val
                if val is None:
                    item['takadoshi_warning'] = True
                    result['warnings'].append(f'{name}: 先月末（{may_last_date}）の最終在庫が空白です。手動で確認してください。')

            for rtype in TRANSFER_TYPES:
                if rtype not in may_rows:
                    item['skipped_types'].append(f'{rtype}（先月に行なし）')
                    continue
                if rtype not in jun_rows:
                    item['skipped_types'].append(f'{rtype}（今月に行なし）')
                    continue
                any_val = False
                for i in range(10):
                    val = may_ws.cell(may_rows[rtype], overlap_start_col + i).value
                    jun_ws.cell(jun_rows[rtype], COL_DAY1 + i).value = val
                    if val is not None:
                        any_val = True
                item['transferred_types'].append(rtype)
                if not any_val:
                    result['warnings'].append(f'{name} / {rtype}: 転記しましたが先月のオーバーフロー欄がすべて空白でした。')

            result['transferred'].append(item)

        # Fuzzy match warning: discontinued name similar to new name → possible rename
        for disc in result['discontinued']:
            for new in result['new_products']:
                ratio = _similarity(disc['name'], new['name'])
                if ratio >= FUZZY_THRESHOLD:
                    result['warnings'].append(
                        f'名前が似ている商品があります（類似度{ratio:.0%}）: '
                        f'廃止「{disc["name"]}」/ 新規「{new["name"]}」 — '
                        f'名前変更の場合は手動で転記してください。'
                    )

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
st.caption('先月のオーバーフロー計画（備考・計画・使用予測）と先月末在庫を今月ファイルに転記します。')

col1, col2 = st.columns(2)
with col1:
    may_file = st.file_uploader('先月ファイル（例: 5月計画.xlsx）', type='xlsx', key='may')
with col2:
    jun_file = st.file_uploader('今月ファイル（例: 6月計画.xlsx）', type='xlsx', key='jun')

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
    def fmt_date(d):
        parts = d.split('-')
        return f"{int(parts[1])}/{int(parts[2])}"

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric('先月末', fmt_date(result['may_last_date']))
    c2.metric('今月開始', fmt_date(result['jun_start']))
    c3.metric('転記済み', len(result['transferred']))
    c4.metric('新規', len(result['new_products']))
    c5.metric('廃止', len(result['discontinued']))

    # Download
    st.download_button(
        '📥 今月ファイルをダウンロード',
        data=result['jun_bytes'],
        file_name=f'merged_{datetime.now().strftime("%Y-%m-%d")}.xlsx',
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
            label = f"✅ {item['name']}" + (f"  [{item['code']}]" if item['code'] else '')
            with st.expander(label):
                st.write(f"**棚卸し前在庫:** {takadoshi}")
                st.write(f"**転記行:** {transferred}")
                if item['skipped_types']:
                    st.write(f"**スキップ:** {skipped}")

    # New products
    if result['new_products']:
        st.subheader('新規商品（在庫数を手動入力してください）')
        for p in result['new_products']:
            label = p['name'] + (f"  [{p['code']}]" if p['code'] else '')
            st.warning(label, icon='🆕')

    # Discontinued
    if result['discontinued']:
        st.subheader('廃止商品（今月ファイルに存在しないためスキップ）')
        for p in result['discontinued']:
            label = p['name'] + (f"  [{p['code']}]" if p['code'] else '')
            st.info(label, icon='🗂️')
