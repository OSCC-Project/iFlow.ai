"""WebSocket 实时推送管理"""
import json, asyncio
from typing import Optional
from fastapi import WebSocket

class WSManager:
    """管理所有 WebSocket 连接，支持按 run_id 分组推送"""

    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}  # run_id → [ws, ...]
        self._user_ws: dict[str, WebSocket] = {}            # user_id → ws (全局通知)

    async def connect(self, ws: WebSocket, run_id: Optional[str] = None):
        await ws.accept()
        if run_id:
            self._connections.setdefault(run_id, []).append(ws)

    async def disconnect(self, ws: WebSocket, run_id: Optional[str] = None):
        if run_id and run_id in self._connections:
            self._connections[run_id] = [w for w in self._connections[run_id] if w != ws]

    async def broadcast(self, run_id: str, event: dict):
        """向订阅某个 run_id 的所有客户端推送事件"""
        for ws in self._connections.get(run_id, []):
            try:
                await ws.send_text(json.dumps(event, default=str))
            except Exception:
                pass

    async def send_step_start(self, run_id: str, step: str):
        await self.broadcast(run_id, {"type": "step_start", "run_id": run_id, "step": step})

    async def send_step_done(self, run_id: str, step: str, duration: float, metrics: dict = None):
        await self.broadcast(run_id, {
            "type": "step_done", "run_id": run_id, "step": step,
            "duration": round(duration, 2), "metrics": metrics or {}
        })

    async def send_log(self, run_id: str, text: str):
        await self.broadcast(run_id, {"type": "log", "run_id": run_id, "text": text})

    async def send_error(self, run_id: str, error: str):
        await self.broadcast(run_id, {"type": "error", "run_id": run_id, "error": error})

    async def send_metric_update(self, run_id: str, metrics: dict):
        await self.broadcast(run_id, {"type": "metric_update", "run_id": run_id, "metrics": metrics})


# 全局单例
ws_manager = WSManager()
