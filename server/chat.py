"""
AI 对话引擎 — 完整项目上下文 + 多轮对话 + 自然决策
"""
import json, os, urllib.request
from server.ai_knowledge import SYSTEM_PROMPT


class ChatSession:
    """多轮对话会话"""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.history: list[dict] = []  # [{role, content}]

    def send(self, user_message: str, context: str = "") -> dict:
        """发送消息，返回 {reply, action}。context 为当前阶段上下文(RTL/SVA/结果等)"""
        full_msg = user_message
        if context:
            full_msg = f"[当前上下文]\n{context}\n\n[用户消息]\n{user_message}"
        self.history.append({"role": "user", "content": full_msg})

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages += self.history[-20:]

        try:
            # deepseek-reasoner: 推理能力强，适合分析芯片设计问题
            # deepseek-chat: 速度快但弱，作为备选
            model = os.environ.get("CHAT_MODEL", "deepseek-reasoner")
            data = json.dumps({
                "model": model,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 2048,
            }).encode()
            req = urllib.request.Request(
                "https://api.deepseek.com/v1/chat/completions",
                data=data,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
            )
            # P2-8: deepseek-reasoner 实测 17s+, 30s 易超时 → 90s
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = json.loads(resp.read())
                reply = body["choices"][0]["message"]["content"]
        except Exception as e:
            # P2-8: 调用失败不写入历史 (避免把错误注入上下文), 也不伪造 assistant 回复
            self.history.pop()  # 回滚刚 append 的用户消息, 下次可重试
            return {"reply": f"(AI 调用失败: {e})", "action": None}

        self.history.append({"role": "assistant", "content": reply})

        # 解析动作
        action = None
        for line in reply.split("\n"):
            if line.strip().startswith("[ACTION:"):
                parts = line.strip()[1:-1].split()
                action = {}
                for p in parts:
                    if ":" in p:
                        k, v = p.split(":", 1)
                        action[k.lower()] = v

        return {"reply": reply, "action": action}


# 全局会话管理
sessions: dict[str, ChatSession] = {}


def get_or_create_session(session_id: str, api_key: str) -> ChatSession:
    if session_id not in sessions:
        sessions[session_id] = ChatSession(api_key)
    return sessions[session_id]
