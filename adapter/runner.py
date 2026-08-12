# ============================================================
# runner.py —— 定义 Backend 接口 + 注册表 + 工厂
# ============================================================
from abc import ABC, abstractmethod
from typing import Optional


# ============================================================
# 第一部分：异常定义
# ============================================================
class BackendError(Exception):
    """后端相关异常的基类"""
    pass


class BackendNotFoundError(BackendError):
    """未找到指定的后端"""
    pass


class BackendExecutionError(BackendError):
    """后端执行失败（工具崩溃、超时等）"""
    pass


# ============================================================
# 第二部分：抽象基类（接口契约）
# ============================================================
class Backend(ABC):
    """强制所有具体 Runner 实现 execute()，保证 Adapter 调用时接口一致"""

    @abstractmethod
    def execute(self, circuit_name: str, params: dict,
                analyses: Optional[list] = None) -> dict:
        """执行仿真/分析，返回原始输出"""
        pass


# ============================================================
# 第三部分：注册表（Registry）
# ============================================================
class BackendRegistry:
    """把"名字 → 类"的映射集中管理，新增工具只需注册，不修改调度代码"""

    _backends: dict = {}  # 注册表：存的是 "名字 → 类" 的映射

    @classmethod
    def register(cls, name: str, backend_class):
        """往注册表里添加"""
        cls._backends[name] = backend_class

    @classmethod
    def get(cls, name: str, config: dict) -> Backend:
        """从注册表里查找并实例化"""
        backend_class = cls._backends.get(name)
        if not backend_class:
            raise BackendNotFoundError(f"未知后端: {name}")
        return backend_class(config)


# ============================================================
# 第四部分：工厂函数（对外入口）
# ============================================================
def create_backend(name: str, config: dict) -> Backend:
    """工厂函数：根据名称创建后端实例"""
    return BackendRegistry.get(name, config)
