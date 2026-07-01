from flask import Flask, request, send_file, render_template_string
from io import BytesIO
from core import run_handoff

app = Flask(__name__)

HTML = '''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>月次引き継ぎ</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: "Hiragino Sans", "Yu Gothic", sans-serif; background: #f4f6f9; color: #222; }
  .container { max-width: 860px; margin: 40px auto; padding: 0 20px 60px; }
  h1 { font-size: 1.4rem; font-weight: 700; margin-bottom: 6px; }
  .sub { color: #666; font-size: 0.9rem; margin-bottom: 32px; }

  .card { background: white; border-radius: 10px; padding: 28px; margin-bottom: 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
  .card h2 { font-size: 1rem; font-weight: 700; margin-bottom: 18px; color: #444; }

  .upload-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .upload-box label { display: block; font-size: 0.82rem; font-weight: 600; color: #555; margin-bottom: 6px; }
  .upload-box input[type=file] { width: 100%; border: 2px dashed #ccd; border-radius: 8px; padding: 12px; font-size: 0.85rem; cursor: pointer; background: #fafbff; }
  .upload-box input[type=file]:hover { border-color: #4472c4; }

  button[type=submit] { margin-top: 20px; width: 100%; padding: 14px; background: #4472c4; color: white; font-size: 1rem; font-weight: 700; border: none; border-radius: 8px; cursor: pointer; }
  button[type=submit]:hover { background: #3560b0; }

  .badge { display: inline-block; padding: 2px 9px; border-radius: 20px; font-size: 0.75rem; font-weight: 700; }
  .badge-ok   { background: #e6f4ea; color: #2d7a3a; }
  .badge-new  { background: #fff3cd; color: #856404; }
  .badge-warn { background: #fff3cd; color: #856404; }
  .badge-skip { background: #f0f0f0; color: #666; }
  .badge-err  { background: #fde8e8; color: #b91c1c; }

  .error-box { background: #fde8e8; border-left: 4px solid #ef4444; border-radius: 6px; padding: 16px 20px; margin-bottom: 20px; }
  .error-box strong { color: #b91c1c; }
  .error-box pre { margin-top: 10px; font-size: 0.75rem; background: #fff5f5; padding: 10px; border-radius: 4px; overflow-x: auto; white-space: pre-wrap; }

  .info-bar { background: #eef2fb; border-radius: 8px; padding: 12px 16px; font-size: 0.85rem; color: #3a3a6a; margin-bottom: 20px; display: flex; gap: 24px; flex-wrap: wrap; }
  .info-bar span strong { font-weight: 700; }

  .warnings-box { background: #fffbeb; border-left: 4px solid #f59e0b; border-radius: 6px; padding: 14px 18px; margin-bottom: 20px; }
  .warnings-box h3 { font-size: 0.88rem; font-weight: 700; color: #92400e; margin-bottom: 8px; }
  .warnings-box ul { padding-left: 18px; font-size: 0.84rem; color: #78350f; line-height: 1.8; }

  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th { background: #f0f4fb; text-align: left; padding: 9px 12px; font-weight: 700; color: #555; border-bottom: 2px solid #dde3f0; }
  td { padding: 9px 12px; border-bottom: 1px solid #eee; vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  .mono { font-family: monospace; font-size: 0.82rem; }

  .section-title { font-size: 0.82rem; font-weight: 700; color: #888; text-transform: uppercase; letter-spacing: 0.06em; margin: 28px 0 10px; }

  .download-btn { display: inline-block; margin-top: 20px; padding: 12px 28px; background: #16a34a; color: white; font-size: 0.95rem; font-weight: 700; border-radius: 8px; text-decoration: none; }
  .download-btn:hover { background: #15803d; }
</style>
</head>
<body>
<div class="container">
  <h1>月次引き継ぎスクリプト</h1>
  <p class="sub">今月のオーバーフロー計画（備考・計画（倍）・使用予測）と今月末在庫を来月ファイルに転記します。</p>

  <div class="card">
    <h2>ファイルを選択</h2>
    <form method="POST" enctype="multipart/form-data">
      <div class="upload-grid">
        <div class="upload-box">
          <label>今月のファイル（記入済み）（例: 6月計画.xlsx）</label>
          <input type="file" name="may_file" accept=".xlsx" required>
        </div>
        <div class="upload-box">
          <label>来月のファイル（空白）（例: 7月計画.xlsx）</label>
          <input type="file" name="jun_file" accept=".xlsx" required>
        </div>
      </div>
      <button type="submit">引き継ぎを実行</button>
    </form>
  </div>

  {% if result %}

    {% if result.error %}
    <div class="error-box">
      <strong>エラー: {{ result.error }}</strong>
      {% if result.detail %}<pre>{{ result.detail }}</pre>{% endif %}
    </div>

    {% else %}

    <div class="info-bar">
      <span><strong>今月:</strong> {{ result.may_start }} 〜 {{ result.may_last_date }}</span>
      <span><strong>来月:</strong> {{ result.jun_start }} 〜</span>
      <span><strong>転記済み:</strong> {{ result.transferred|length }} 商品</span>
      <span><strong>新規:</strong> {{ result.new_products|length }} 商品</span>
      <span><strong>廃止:</strong> {{ result.discontinued|length }} 商品</span>
    </div>

    {% if result.warnings %}
    <div class="warnings-box">
      <h3>⚠ 確認が必要な項目 ({{ result.warnings|length }}件)</h3>
      <ul>{% for w in result.warnings %}<li>{{ w }}</li>{% endfor %}</ul>
    </div>
    {% endif %}

    <a class="download-btn" href="/download">来月のファイルをダウンロード（記入済み）</a>

    {% if result.transferred %}
    <div class="section-title">転記済み商品</div>
    <div class="card" style="padding:0;overflow:hidden">
      <table>
        <thead><tr>
          <th>コード</th><th>商品名</th><th>棚卸し前在庫</th><th>転記した行</th><th>スキップ</th>
        </tr></thead>
        <tbody>
        {% for item in result.transferred %}
        <tr>
          <td class="mono">{{ item.code }}</td>
          <td>{{ item.name }}</td>
          <td>
            {% if item.takadoshi_mae is none %}
              <span class="badge badge-warn">空白</span>
            {% else %}
              {{ item.takadoshi_mae }}
              {% if item.takadoshi_warning %}<span class="badge badge-warn">要確認</span>{% endif %}
            {% endif %}
          </td>
          <td>
            {% for t in item.transferred_types %}
              <span class="badge badge-ok">{{ t }}</span>
            {% endfor %}
          </td>
          <td>
            {% for s in item.skipped_types %}
              <span class="badge badge-skip">{{ s }}</span>
            {% endfor %}
          </td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
    {% endif %}

    {% if result.new_products %}
    <div class="section-title">新規商品（今月データなし・手動入力が必要）</div>
    <div class="card" style="padding:0;overflow:hidden">
      <table>
        <thead><tr><th>コード</th><th>商品名</th><th>対応</th></tr></thead>
        <tbody>
        {% for p in result.new_products %}
        <tr>
          <td class="mono">{{ p.code }}</td>
          <td>{{ p.name }}</td>
          <td><span class="badge badge-new">在庫数を手動入力してください</span></td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
    {% endif %}

    {% if result.discontinued %}
    <div class="section-title">廃止商品（来月ファイルに存在しないためスキップ）</div>
    <div class="card" style="padding:0;overflow:hidden">
      <table>
        <thead><tr><th>コード</th><th>商品名</th></tr></thead>
        <tbody>
        {% for p in result.discontinued %}
        <tr>
          <td class="mono">{{ p.code }}</td>
          <td>{{ p.name }}</td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
    {% endif %}

    {% endif %}
  {% endif %}
</div>
</body>
</html>'''


_last_result_bytes = None
_last_jun_filename = 'jun_output移行済.xlsx'


@app.route('/', methods=['GET', 'POST'])
def index():
    global _last_result_bytes, _last_jun_filename
    result = None

    if request.method == 'POST':
        may_file = request.files.get('may_file')
        jun_file = request.files.get('jun_file')

        if not may_file or not jun_file:
            result = {'error': 'ファイルが選択されていません。'}
        else:
            result = run_handoff(may_file.read(), jun_file.read())
            if result.get('jun_bytes'):
                _last_result_bytes = result['jun_bytes']
                stem = (jun_file.filename or 'jun_output').rsplit('.', 1)[0]
                _last_jun_filename = f'{stem}移行済.xlsx'

    return render_template_string(HTML, result=result)


@app.route('/download')
def download():
    if not _last_result_bytes:
        return 'ファイルがありません。先に引き継ぎを実行してください。', 404
    return send_file(
        BytesIO(_last_result_bytes),
        as_attachment=True,
        download_name=_last_jun_filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


if __name__ == '__main__':
    print("起動中: http://localhost:5001")
    app.run(debug=True, port=5001)
