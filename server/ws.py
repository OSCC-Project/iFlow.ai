"""WebSocket 实时推送管理"""
import json, asyncio, threading
from typing import Optional
from fastapi import WebSocket

class WSManager:
    """管理所有 WebSocket 连接，支持按 run_id 分组推送

    push_ws 回调在业务线程里每次 asyncio.run() 独立发送:
    - 同一连接可能同时被 run_id 频道和 global 频道的两个线程写
    - Starlette WebSocket 非线程安全 → 每连接一把线程锁串行化发送,
      否则并发写同一 socket 会帧交错 (浏览器报 Invalid frame header)
    """

    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}  # run_id → [ws, ...]
        self._user_ws: dict[str, WebSocket] = {}            # user_id → ws (全局通知)
        self._send_locks: dict[int, threading.Lock] = {}    # id(ws) → 发送锁
        self._guard = threading.Lock()

    def _lock_for(self, ws: WebSocket) -> threading.Lock:
        key = id(ws)
        with self._guard:
            lock = self._send_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._send_locks[key] = lock
            return lock

    async def connect(self, ws: WebSocket, run_id: Optional[str] = None):
        await ws.accept()
        if run_id:
            self._connections.setdefault(run_id, []).append(ws)

    async def disconnect(self, ws: WebSocket, run_id: Optional[str] = None):
        if run_id and run_id in self._connections:
            self._connections[run_id] = [w for w in self._connections[run_id] if w != ws]
            if not self._connections[run_id]:
                del self._connections[run_id]
        # 连接彻底关闭后释放发送锁 (避免长会话下锁字典无限增长)
        if not any(ws in v for v in self._connections.values()):
            with self._guard:
                self._send_locks.pop(id(ws), None)

    async def broadcast(self, run_id: str, event: dict):
        """向订阅某个 run_id 的所有客户端推送事件 (串行化每连接发送)"""
        text = json.dumps(event, default=str)
        for ws in list(self._connections.get(run_id, [])):
            lock = self._lock_for(ws)
            with lock:
                try:
                    await ws.send_text(text)
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
