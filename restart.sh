#!/bin/bash
# iflow-lab 服务重启脚本: 后端 (uvicorn:8000) + 前端 (next dev:3000)
# 用法: bash restart.sh [backend|frontend|all]  (默认 all)
set -e
cd "$(dirname "$0")"

stop_port() {
  local pid
  pid=$(lsof -ti:"$1" 2>/dev/null || true)
  [ -n "$pid" ] && kill $pid 2>/dev/null && sleep 1 || true
}

start_backend() {
  echo "▶ 重启后端 (uvicorn :8000)..."
  stop_port 8000
  # 必须在项目根目录启动 (否则报 ModuleNotFoundError: No module named 'server')
  setsid nohup uvicorn server.api:app --host 0.0.0.0 --port 8000 \
    > /tmp/uvicorn.log 2>&1 < /dev/null &
  sleep 2
  curl -s -o /dev/null -w "  后端状态: %{http_code}\n" http://localhost:8000/api/health || echo "  后端启动失败, 看日志: tail /tmp/uvicorn.log"
}

start_frontend() {
  echo "▶ 重启前端 (next dev :3000)..."
  stop_port 3000
  cd frontend
  setsid nohup npm run dev > /tmp/nextdev.log 2>&1 < /dev/null &
  sleep 5
  curl -s -o /dev/null -w "  前端状态: %{http_code}\n" http://localhost:3000/ || echo "  前端启动失败, 看日志: tail /tmp/nextdev.log"
}

case "${1:-all}" in
  backend)  start_backend ;;
  frontend) start_frontend ;;
  all)      start_backend; start_frontend ;;
  *) echo "用法: bash restart.sh [backend|frontend|all]"; exit 1 ;;
esac
