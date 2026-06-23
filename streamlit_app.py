import streamlit as st
from io import BytesIO
from datetime import datetime, timedelta, date
import traceback
import openpyxl
from openpyxl.cell.cell import MergedCell

COL_CODE   = 5
COL_NAME   = 11  # 品目名
COL_H      = 7   # 棚卸し前在庫
COL_L      = 12  # ロット（一回あたり）
COL_R      = 18  # 入庫リードタイム（日）
COL_RTYPE  = 20  # 行種別
COL_DAY1        = 21
MAX_LEAD_TIME   = 6   # cap for 入庫予定数 blind-spot fill
TRANSFER_TYPES  = ['備考', '計画（倍）', '使用予測']


def parse_date(val):
    if isinstance(val, datetime):
        return val.date()
    return None


def safe_write(ws, row, col, val):
    cell = ws.cell(row, col)
    if not isinstance(cell, MergedCell):
        cell.value = val


def find_planning_sheet(wb):
    """Return the first sheet that has a date in row 2, col COL_DAY1 (U2)."""
    for ws in wb.worksheets:
        if isinstance(ws.cell(2, COL_DAY1).value, datetime):
            return ws
    return wb.active


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
        if not current_code:
            continue
        if current_code not in products:
            products[current_code] = {'name': None, 'blocks': [{}]}
        p = products[current_code]
        # Each 備考 row starts a new block (except the very first)
        if rtype == '備考' and p['blocks'][-1]:
            p['blocks'].append({})
        p['blocks'][-1][rtype] = row
        if rtype == '備考':
            p['blocks'][-1]['_lot_size']  = ws.cell(row, COL_L).value or 0
            p['blocks'][-1]['_lead_time'] = int(ws.cell(row, COL_R).value or 0)
            if code:
                p['name'] = ws.cell(row, COL_NAME).value
    return products


def run_handoff(may_bytes, jun_bytes, may_sheet=None, jun_sheet=None):
    result = {
        'success': False, 'error': None, 'detail': None,
        'may_start': None, 'jun_start': None, 'may_last_date': None,
        'transferred': [], 'new_products': [], 'discontinued': [], 'warnings': [],
        'jun_bytes': None,
    }
    try:
        may_wb = openpyxl.load_workbook(BytesIO(may_bytes), data_only=True)
        jun_wb = openpyxl.load_workbook(BytesIO(jun_bytes))
        may_ws = may_wb[may_sheet] if may_sheet else find_planning_sheet(may_wb)
        jun_ws = jun_wb[jun_sheet] if jun_sheet else find_planning_sheet(jun_wb)

        may_start = parse_date(may_ws.cell(2, COL_DAY1).value)
        jun_start = parse_date(jun_ws.cell(2, COL_DAY1).value)

        if not may_start:
            result['error'] = f'今月ファイルのシート「{may_ws.title}」のU2セルに日付が見つかりません。日付形式を確認してください。'
            return result
        if not jun_start:
            result['error'] = f'来月ファイルのシート「{jun_ws.title}」のU2セルに日付が見つかりません。日付形式を確認してください。'
            return result
        if jun_start <= may_start:
            result['error'] = f'来月の開始日（{jun_start}）が今月の開始日（{may_start}）より前です。ファイルの順番を確認してください。'
            return result

        result['may_start']     = str(may_start)
        result['jun_start']     = str(jun_start)
        may_last_date           = jun_start - timedelta(days=1)
        result['may_last_date'] = str(may_last_date)

        is_last_day = date.today() == may_last_date

        may_last_col      = COL_DAY1 + (may_last_date - may_start).days
        overlap_start_col = COL_DAY1 + (jun_start     - may_start).days

        if overlap_start_col > may_ws.max_column:
            result['error'] = f'今月ファイルにオーバーフロー列（{jun_start}以降）が見つかりません。今月ファイルに翌月分の列が含まれているか確認してください。'
            return result

        overlap_days = min(
            may_ws.max_column - overlap_start_col + 1,
            jun_ws.max_column - COL_DAY1 + 1,
        )

        may_products = build_product_map(may_ws)
        jun_products = build_product_map(jun_ws)

        if not may_products:
            result['error'] = '今月ファイルにコード（E列）が入力された商品が見つかりません。'
            return result
        if not jun_products:
            result['error'] = '来月ファイルにコード（E列）が入力された商品が見つかりません。'
            return result

        for code, info in may_products.items():
            if code not in jun_products:
                result['discontinued'].append({'code': code, 'name': info.get('name') or '（名前なし）'})

        for code, jun_info in jun_products.items():
            name = jun_info['name'] or '（名前なし）'
            if code not in may_products:
                result['new_products'].append({'code': code, 'name': name})
                continue

            may_blocks = may_products[code]['blocks']
            jun_blocks = jun_info['blocks']
            item = {
                'code': code, 'name': name,
                'takadoshi_mae': None, 'takadoshi_warning': False,
                'transferred_types': [], 'skipped_types': [],
            }
            nyuko_written_dates = []

            for b_idx in range(min(len(may_blocks), len(jun_blocks))):
                may_block = may_blocks[b_idx]
                jun_block = jun_blocks[b_idx]

                if is_last_day:
                    if '最終' not in may_block:
                        result['warnings'].append(f'[{code}] {name} ブロック{b_idx+1}: 今月に最終行が見つかりません。棚卸し前在庫をスキップしました。')
                    elif '備考' not in jun_block:
                        result['warnings'].append(f'[{code}] {name} ブロック{b_idx+1}: 来月に備考行が見つかりません。棚卸し前在庫をスキップしました。')
                    else:
                        val = may_ws.cell(may_block['最終'], may_last_col).value
                        safe_write(jun_ws, jun_block['備考'], COL_H, val)
                        if b_idx == 0:
                            item['takadoshi_mae'] = val
                        if val is None:
                            item['takadoshi_warning'] = True
                            result['warnings'].append(f'[{code}] {name} ブロック{b_idx+1}: 今月末（{may_last_date}）の最終在庫が空白です。手動で確認してください。')

                for rtype in TRANSFER_TYPES:
                    if rtype not in may_block:
                        if b_idx == 0:
                            item['skipped_types'].append(f'{rtype}（今月に行なし）')
                        continue
                    if rtype not in jun_block:
                        if b_idx == 0:
                            item['skipped_types'].append(f'{rtype}（来月に行なし）')
                        continue
                    any_val = False
                    for i in range(overlap_days):
                        val = may_ws.cell(may_block[rtype], overlap_start_col + i).value
                        safe_write(jun_ws, jun_block[rtype], COL_DAY1 + i, val)
                        if val is not None:
                            any_val = True
                    if b_idx == 0:
                        item['transferred_types'].append(rtype)
                    if not any_val and b_idx == 0:
                        result['warnings'].append(f'[{code}] {name} / {rtype}: 転記しましたが今月のオーバーフロー欄がすべて空白でした。')

                # Fill 入庫予定数 for the first lead_time days of 来月 (formula blind spot)
                lead_time = min(may_block.get('_lead_time', 0), MAX_LEAD_TIME)
                lot_size  = may_block.get('_lot_size',  0)
                if lead_time and lot_size and '入庫予定数' in jun_block and '計画（倍）' in may_block:
                    for d in range(lead_time):
                        may_col = overlap_start_col - lead_time + d
                        if may_col < COL_DAY1:
                            continue
                        keikaku = may_ws.cell(may_block['計画（倍）'], may_col).value
                        if keikaku:
                            safe_write(jun_ws, jun_block['入庫予定数'], COL_DAY1 + d, keikaku * lot_size)
                            if b_idx == 0:
                                nyuko_written_dates.append(jun_start + timedelta(days=d))

            if nyuko_written_dates:
                first = nyuko_written_dates[0]
                last  = nyuko_written_dates[-1]
                if first == last:
                    label = f'入庫予定数（{first.month}/{first.day}）'
                else:
                    label = f'入庫予定数（{first.month}/{first.day}〜{last.month}/{last.day}）'
                item['transferred_types'].append(label)

            result['transferred'].append(item)

        # Remove sheet protection so the output file is editable in Excel
        jun_ws.protection.sheet = False

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

with st.expander('このアプリの使い方', expanded=False):
    st.markdown("""
**月末に今月のデータを来月ファイルへ引き継ぐツールです。**

#### 転記される内容
| 項目 | 説明 |
|------|------|
| 備考 | 今月ファイルの来月分オーバーフロー欄の備考を転記 |
| 計画（倍） | 今月ファイルの来月分オーバーフロー計画を転記 |
| 使用予測 | 今月ファイルの来月分オーバーフロー使用予測を転記 |
| 入庫予定数 | 入庫リードタイム（R列）分だけ来月初日の入庫予定を補正（最大6日） |
| 棚卸し前在庫 | 月末最終日のみ・今月最終在庫を来月ファイルの棚卸し前在庫欄に転記 |

#### 手順
1. **今月のファイル（記入済み）** と **来月のファイル（空白テンプレート）** をアップロード
2. 複数シートがある場合はドロップダウンで対象シートを選択（自動検出）
3. 「引き継ぎを実行」をクリック
4. 結果を確認し、**来月のファイルをダウンロード（記入済み）** で保存

#### 注意事項
- 来月ファイルのシート保護は自動的に解除されます
- ダウンロードされるファイル名は元のファイル名に「移行済」が付きます
- 新規商品・廃止商品は自動では処理されないため、手動対応が必要です
""")

col1, col2 = st.columns(2)
with col1:
    may_file = st.file_uploader('今月のファイル（記入済み）（例: 6月計画.xlsx）', type='xlsx', key='may')
with col2:
    jun_file = st.file_uploader('来月のファイル（空白）（例: 7月計画.xlsx）', type='xlsx', key='jun')

may_sheet = None
jun_sheet = None

if may_file and jun_file:
    may_bytes = may_file.read()
    jun_bytes = jun_file.read()

    may_wb_peek = openpyxl.load_workbook(BytesIO(may_bytes), read_only=True, data_only=True)
    jun_wb_peek = openpyxl.load_workbook(BytesIO(jun_bytes), read_only=True, data_only=True)

    may_sheets = may_wb_peek.sheetnames
    jun_sheets = jun_wb_peek.sheetnames

    may_default = find_planning_sheet(openpyxl.load_workbook(BytesIO(may_bytes), data_only=True)).title
    jun_default = find_planning_sheet(openpyxl.load_workbook(BytesIO(jun_bytes), data_only=True)).title

    col3, col4 = st.columns(2)
    with col3:
        may_sheet = st.selectbox('今月：使用するシート', may_sheets, index=may_sheets.index(may_default))
    with col4:
        jun_sheet = st.selectbox('来月：使用するシート', jun_sheets, index=jun_sheets.index(jun_default))

if st.button('引き継ぎを実行', type='primary', disabled=not (may_file and jun_file)):
    with st.spinner('処理中...'):
        result = run_handoff(may_bytes, jun_bytes, may_sheet, jun_sheet)

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

    # Download — filename: <original_stem>移行済.xlsx
    stem = jun_file.name.rsplit('.', 1)[0]
    st.download_button(
        '📥 来月のファイルをダウンロード（記入済み）',
        data=result['jun_bytes'],
        file_name=f'{stem}移行済.xlsx',
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        type='primary',
    )

    # Warnings
    if result['warnings']:
        with st.expander(f'⚠️ 確認が必要な項目 ({len(result["warnings"])}件)', expanded=True):
            for w in result['warnings']:
                st.warning(w, icon='⚠️')

    # Transferred products
    if result['transferred']:
        with st.expander(f'✅ 転記済み商品 ({len(result["transferred"])}件)', expanded=False):
            rows = []
            for item in result['transferred']:
                rows.append({
                    'コード': item['code'],
                    '品目名': item['name'],
                    '転記した行': '、'.join(item['transferred_types']) if item['transferred_types'] else '（なし）',
                    'スキップした行': '、'.join(item['skipped_types']) if item['skipped_types'] else '―',
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)

    if result['new_products']:
        with st.expander(f'🆕 新規商品 ({len(result["new_products"])}件) — 在庫数を手動入力してください', expanded=False):
            st.dataframe(result['new_products'], use_container_width=True, hide_index=True)

    if result['discontinued']:
        with st.expander(f'🗑️ 廃止商品 ({len(result["discontinued"])}件) — 来月ファイルに存在しないためスキップ', expanded=False):
            st.dataframe(result['discontinued'], use_container_width=True, hide_index=True)
