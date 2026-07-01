"""
月次引き継ぎスクリプト（CLI）
使い方: python3 handoff.py <今月ファイル.xlsx> <来月ファイル.xlsx>
"""

import sys
from core import run_handoff


def main(may_path, jun_path):
    may_bytes = open(may_path, 'rb').read()
    jun_bytes = open(jun_path, 'rb').read()

    result = run_handoff(may_bytes, jun_bytes)

    if result['error']:
        print(f"ERROR: {result['error']}")
        if result['detail']:
            print(result['detail'])
        sys.exit(1)

    print(f"今月: {result['may_start']} 〜 {result['may_last_date']}")
    print(f"来月: {result['jun_start']} 〜")

    for p in result['discontinued']:
        print(f"  廃止: [{p['code']}] {p['name']}")

    for p in result['new_products']:
        print(f"  新規（手動入力が必要）: [{p['code']}] {p['name']}")

    for item in result['transferred']:
        types = '、'.join(item['transferred_types']) if item['transferred_types'] else '（なし）'
        print(f"  転記済み: [{item['code']}] {item['name']}  →  {types}")

    if result['warnings']:
        print(f"\n警告 ({len(result['warnings'])}件):")
        for w in result['warnings']:
            print(f"  ⚠ {w}")

    stem = jun_path.rsplit('.', 1)[0]
    out_path = f'{stem}移行済.xlsx'
    with open(out_path, 'wb') as f:
        f.write(result['jun_bytes'])

    print(f"\n出力ファイル: {out_path}")


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("使い方: python3 handoff.py <今月.xlsx> <来月.xlsx>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
