"""JWT 用户认证"""
import hashlib, secrets, time, sqlite3
from dataclasses import dataclass
from typing import Optional

@dataclass
class User:
    id: str
    username: str
    role: str  # "student" | "teacher" | "admin"

class AuthManager:
    """简易 JWT-like token 认证 (零外部依赖)"""

    def __init__(self, db_path: str = "./server/users.db", secret: str = ""):
        self.db_path = db_path
        self.secret = secret or secrets.token_hex(32)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, username TEXT UNIQUE,
                password_hash TEXT, role TEXT DEFAULT 'student',
                created_at REAL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY, user_id TEXT,
                expires_at REAL)""")

    def register(self, username: str, password: str, role: str = "student") -> Optional[User]:
        uid = hashlib.sha256(username.encode()).hexdigest()[:16]
        pw_hash = hashlib.sha256(f"{password}:{self.secret}".encode()).hexdigest()
        try:
            with sqlite3.connect(self.db_path) as db:
                db.execute("INSERT INTO users VALUES(?,?,?,?,?)",
                           [uid, username, pw_hash, role, time.time()])
            return User(id=uid, username=username, role=role)
        except sqlite3.IntegrityError:
            return None

    def login(self, username: str, password: str) -> Optional[str]:
        pw_hash = hashlib.sha256(f"{password}:{self.secret}".encode()).hexdigest()
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT id, role FROM users WHERE username=? AND password_hash=?",
                             [username, pw_hash]).fetchone()
        if not row:
            return None
        token = secrets.token_urlsafe(32)
        expires = time.time() + 86400 * 7  # 7 天
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO sessions VALUES(?,?,?)", [token, row[0], expires])
        return token

    def verify(self, token: str) -> Optional[User]:
        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "SELECT u.id, u.username, u.role FROM sessions s "
                "JOIN users u ON s.user_id = u.id "
                "WHERE s.token=? AND s.expires_at > ?",
                [token, time.time()]).fetchone()
        return User(id=row[0], username=row[1], role=row[2]) if row else None

    def logout(self, token: str):
        with sqlite3.connect(self.db_path) as db:
            db.execute("DELETE FROM sessions WHERE token=?", [token])
