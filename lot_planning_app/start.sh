#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== ロット計画アプリ 起動 ==="

# Backend
cd "$SCRIPT_DIR/backend"
if [ ! -d ".venv" ]; then
  echo ">>> Python仮想環境を作成中..."
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -q -r requirements.txt
else
  source .venv/bin/activate
fi

echo ">>> バックエンド起動 (http://localhost:8000)"
uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# Frontend
cd "$SCRIPT_DIR/frontend"
if [ ! -d "node_modules" ]; then
  echo ">>> npm install 中..."
  npm install
fi

echo ">>> フロントエンド起動 (http://localhost:5173)"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "起動完了:"
echo "  ブラウザ:  http://localhost:5173"
echo "  API:       http://localhost:8000/docs"
echo ""
echo "停止するには Ctrl+C を押してください"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
