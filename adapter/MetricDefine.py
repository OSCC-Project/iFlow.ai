#读 metric_define.yaml→ 校验格式 → 提供一个 get_circuit_metrics() 方法
#输出：规则字典（Python dict）被 adapter.py 调用
import yaml

class MetricDefineError(Exception):
    """配置校验失败时抛出的异常"""
    pass

class MetricDefine:
    def __init__(self, config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            raw_config = yaml.safe_load(f)
        # 加载后立即校验
        self._validate(raw_config)
        self.config = raw_config

    def _validate(self, raw):
        """校验 YAML 格式是否正确"""
        # 1. 必须有 circuits 字段
        if "circuits" not in raw:
            raise MetricDefineError("YAML 缺少顶级 'circuits' 字段")
        if not isinstance(raw["circuits"], dict):
            raise MetricDefineError("'circuits' 必须是字典")
        
        for name, rules in raw["circuits"].items():
            # 2. 每个电路必须有 metrics 字段
            if "metrics" not in rules:
                raise MetricDefineError(f"电路 '{name}' 缺少 'metrics' 字段")
            if not isinstance(rules["metrics"], dict):
                raise MetricDefineError(f"电路 '{name}' 的 'metrics' 必须是字典")
            
            for metric_name, rule in rules["metrics"].items():
                # 3. 每个指标必须有 source 和 expression
                if "source" not in rule:
                    raise MetricDefineError(
                        f"电路 '{name}' 的指标 '{metric_name}' 缺少 'source'"
                    )
                if "expression" not in rule:
                    raise MetricDefineError(
                        f"电路 '{name}' 的指标 '{metric_name}' 缺少 'expression'"
                    )
                # 4. 可选校验：source 是否合法（ac/dc/tran）
                if rule["source"] not in ["ac", "dc", "tran", "sta"]:
                    raise MetricDefineError(
                        f"电路 '{name}' 的指标 '{metric_name}' 的 source 必须是 "
                        f"'ac' / 'dc' / 'tran' / 'sta'"
                    )

    def get_circuit_metrics(self, circuit_name: str) -> dict:
        """返回某个电路的指标规则列表，供 MetricParser 使用。
        找不到时使用 _default 规则，避免 metric_error 阻断流程。"""
        circuits = self.config.get("circuits", {})
        rules = circuits.get(circuit_name, {}).get("metrics", {})
        if not rules:
            rules = circuits.get("_default", {}).get("metrics", {})
        return rules