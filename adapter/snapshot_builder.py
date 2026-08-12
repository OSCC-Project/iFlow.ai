# SnapshotBuilder —— 构建完整 SnapshotPackage (v1.0)
import hashlib, os, uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from .contract import *
from .metric_registry import canonicalize

# 观测级别映射表
_OBS_LEVEL_MAP = {"artifact": "0", "metric": "1", "object": "2", "execution": "3"}

# ═══════════════════════════════════════════════════════════
# Gate Check (文档 3.4): 流程卫士, 检测静默失败
# ═══════════════════════════════════════════════════════════
import re as _re

_GATE_CHECKS = {
    "synthesis": {
        "min_artifacts": 1,
        "required_artifact_pattern": r".*\.v$",
        "min_artifact_size": 512,
        "log_forbidden": ["ERROR:", "FATAL:", "syntax error"],
    },
    "_default_physical": {
        "min_artifacts": 1,
        "min_artifact_size": 1024,
        "log_forbidden": ["ERROR:", "FATAL:", "violation"],
    },
}

def gate_check(stage: str, raw: dict, artifacts: list) -> list[str]:
    """检查产出物是否有效。返回问题列表, 空列表=通过。"""
    issues = []
    check = _GATE_CHECKS.get(stage) or _GATE_CHECKS["_default_physical"]

    # 产出物数量
    if len(artifacts) < check.get("min_artifacts", 0):
        issues.append(f"[{stage}] 产出物数量 {len(artifacts)} < 最低 {check['min_artifacts']}")

    # 产出物大小
    for a in artifacts:
        if a.size < check.get("min_artifact_size", 0):
            issues.append(f"[{stage}] {a.logical_name} 大小 {a.size} bytes < {check['min_artifact_size']}")

    # 日志关键字
    forbidden = check.get("log_forbidden", [])
    stdout = raw.get("stdout", "")
    stderr = raw.get("stderr", "")
    for kw in forbidden:
        if kw.lower() in stdout.lower() or kw.lower() in stderr.lower():
            issues.append(f"[{stage}] 日志含禁止关键字: '{kw}'")

    return issues


class SnapshotBuilder:
    """从 Runner 原始输出构建结构化 SnapshotPackage。"""

    def build(
        self, raw: dict, metrics: Dict[str, Dict[str, float]],
        design_type: str, circuit_name: str,
        parent_snapshot_id: str = "",
        observation_level: str = "1",
        snapshot_type: str = "CHECKPOINT",
    ) -> SnapshotPackage:
        run_id = raw.get("run_id", str(uuid.uuid4()))
        obs_level = _OBS_LEVEL_MAP.get(observation_level, observation_level)

        return SnapshotPackage(
            header=self._build_header(design_type, circuit_name, run_id,
                                       parent_snapshot_id, obs_level, snapshot_type),
            capability=self._build_capability(design_type, raw),
            observation_context=self._build_context(design_type, raw, obs_level, snapshot_type),
            digital_twin=self._build_digital_twin(raw, metrics, design_type, circuit_name),
            artifact_manifest=self._build_artifacts(raw, design_type),
            execution_trace=self._build_execution_trace(raw),
            optimizer_hints=raw.get("optimizer_hints"),
        )

    # ── Header (v1.0: +design_name, +design_type, +schema_version) ──
    # adapter名 → 工具名 映射
    _ADAPTER_TOOL = {"digital":"Yosys","openroad":"OpenROAD","opensta":"OpenSTA",
                     "ieda":"iEDA","analog":"ngspice","primetime":"PrimeTime"}

    def _build_header(self, design_type, circuit_name, run_id,
                      parent_snapshot_id, obs_level, snap_type):
        tool_name = self._ADAPTER_TOOL.get(design_type, design_type)
        return SnapshotHeader(
            snapshot_id=f"snap_{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            parent_snapshot_id=parent_snapshot_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            tool=tool_name, tool_version="1.0.0", adapter_version="0.3.0",
            design_name=circuit_name,
            design_type=design_type,
            stage=design_type, step=0,
            schema_version="1.0",
            snapshot_type=snap_type,
            observation_level=obs_level,
        )

    # ── Capability (v1.0: object→object_delta, execution→execution_trace, +waveform) ──
    def _build_capability(self, design_type, raw):
        caps = {
            "digital":   dict(adapter="digital_adapter", artifact=True, metric=True,
                              object_delta=False, execution_trace=False),
            "ieda":      dict(adapter="ieda_adapter", artifact=True, metric=True,
                              object_delta=True, execution_trace=False),
            "openroad":  dict(adapter="openroad_adapter", artifact=True, metric=True,
                              object_delta=True, execution_trace=True),
            "analog":    dict(adapter="analog_adapter", artifact=True, metric=True,
                              object_delta=False, execution_trace=False, waveform=True),
            "primetime": dict(adapter="primetime_adapter", artifact=True, metric=True,
                              object_delta=False, execution_trace=False),
            "opensta":   dict(adapter="opensta_adapter", artifact=True, metric=True,
                              object_delta=False, execution_trace=False),
        }
        c = caps.get(design_type, dict(adapter=f"{design_type}_adapter",
                                        artifact=True, metric=True))
        return Capability(**c)

    # ── ObservationContext (v1.0: +stage, +trace, +metrics_snapshot) ──
    def _build_context(self, design_type, raw, obs_level, snap_type):
        return ObservationContext(
            stage=raw.get("stage", design_type),
            operation=raw.get("stage", design_type),
            command=raw.get("command", ""),
            parameters=raw.get("params", {}),
            duration_ms=raw.get("duration_ms", 0.0),
            trigger=raw.get("trigger", "scheduled_checkpoint"),
            work_dir=raw.get("run_dir", raw.get("work_dir", "")),
            trace=[],
            metrics_snapshot=raw.get("sta", {}),
        )

    # ── DigitalTwin (v1.0: +design: DesignInfo) ──
    def _build_digital_twin(self, raw, metrics, design_type, circuit_name):
        meta = raw.get("metadata", {})
        if not meta:
            meta = {"design": circuit_name, "technology": "sky130", "flow": design_type}
        objects = []
        for obj in raw.get("objects", []):
            if isinstance(obj, DesignObject):
                objects.append(obj)
            elif isinstance(obj, dict):
                objects.append(DesignObject(
                    id=obj.get("id", ""), type=obj.get("type", "cell"),
                    master=obj.get("master", ""), properties=obj.get("properties", {}),
                ))
        constraints = raw.get("constraints", {})
        if not constraints:
            for key in ("CLK_PERIOD", "UTILIZATION", "MAX_FANOUT",
                        "DIE_AREA", "CORE_AREA", "supply_voltage"):
                if key in raw.get("params", {}):
                    constraints[key.lower()] = raw["params"][key]
        extensions = {}
        for k in ("stdout", "stderr"):
            v = raw.get(k, "")
            if v: extensions[f"{k}_tail"] = v[-2000:] if len(v) > 2000 else v
        for k in ("report_path", "log_path", "sta_report"):
            if k in raw and raw[k]: extensions[k] = raw[k]
        if "extensions" in raw: extensions.update(raw["extensions"])

        return DigitalTwin(
            design=DesignInfo(name=circuit_name,
                              technology=meta.get("technology", "sky130"),
                              top=raw.get("params", {}).get("TOP_MODULE",
                                     raw.get("params", {}).get("DESIGN_TOP", circuit_name))),
            metadata=meta,
            objects=objects,
            metrics=canonicalize(metrics, tool=design_type),
            constraints=constraints,
            extensions=extensions,
        )

    # ── Artifact Manifest ──
    def _build_artifacts(self, raw, design_type="digital"):
        artifacts = []
        # 已知产物键 (netlist_path 仅 synthesis 产出, 物理步骤不重复输出)
        _is_synth = (design_type == "digital")
        defs_raw = {
            "netlist_path":  ("netlist",       "yosys",     "synthesis", []),
            "report_path":   ("timing_report", "sta",       "sta",      ["netlist"]),
            "log_path":      ("sim_log",       "simulator", "simulation", []),
            "sta_report":    ("sta_report",    "sta",       "sta",      ["netlist"]),
        }
        for key, (name, prod, stg, deps) in defs_raw.items():
            if key == "netlist_path" and not _is_synth:
                continue  # 非 synthesis 步骤不把网表当产出
            path = raw.get(key, "")
            if not path or not os.path.exists(path): continue
            try:
                stat = os.stat(path)
                with open(path, "rb") as f:
                    chk = hashlib.sha256(f.read()).hexdigest()[:16]
            except (OSError, PermissionError): continue
            artifacts.append(ArtifactInfo(
                artifact_id=f"art_{uuid.uuid4().hex[:8]}", type="file",
                logical_name=name, source_uri=path, size=stat.st_size,
                checksum=chk, producer=prod, stage=stg, depends_on=deps,
            ))
        # 扫描 run_dir 下的 DEF/GDS/timing 文件 (OpenROAD/iEDA/gds 产出)
        run_dir = raw.get("run_dir", "")
        if run_dir:
            out_dir = os.path.join(run_dir, "output")
            def_files = {
                "floorplan.def": ("floorplan_def","openroad","floorplan"),
                "tapcell.def":   ("tapcell_def","openroad","tapcell"),
                "pdn.def":       ("pdn_def","openroad","pdn"),
                "gplace.def":    ("gplace_def","openroad","gplace"),
                "resize.def":    ("resize_def","openroad","resize"),
                "dplace.def":    ("dplace_def","openroad","dplace"),
                "cts.def":       ("cts_def","openroad","cts"),
                "groute.def":    ("groute_def","openroad","groute"),
                "droute.def":    ("droute_def","openroad","droute"),
                "filler.def":    ("filler_def","openroad","filler"),
            }
            # GDS: 在 run_dir 根目录或 output/ 下都扫描
            for search_dir in (run_dir, out_dir):
                if not os.path.isdir(search_dir): continue
                for fname in os.listdir(search_dir):
                    if fname.endswith(".gds"):
                        fpath = os.path.join(search_dir, fname)
                        try:
                            st = os.stat(fpath); ch = hashlib.sha256(open(fpath,"rb").read()).hexdigest()[:16]
                            artifacts.append(ArtifactInfo(artifact_id=f"art_{uuid.uuid4().hex[:8]}",
                                type="file",logical_name="gds",source_uri=fpath,size=st.st_size,
                                checksum=ch,producer="gds",stage="gds",depends_on=[]))
                        except: pass
            if os.path.isdir(out_dir):
                for fname, (lname, prod, stage) in def_files.items():
                    fpath = os.path.join(out_dir, fname)
                    if os.path.exists(fpath):
                        try:
                            stat = os.stat(fpath)
                            with open(fpath, "rb") as f:
                                chk = hashlib.sha256(f.read()).hexdigest()[:16]
                            artifacts.append(ArtifactInfo(
                                artifact_id=f"art_{uuid.uuid4().hex[:8]}", type="file",
                                logical_name=lname, source_uri=fpath, size=stat.st_size,
                                checksum=chk, producer=prod, stage=stage, depends_on=[],
                            ))
                        except (OSError, PermissionError): pass
                # 时序报告可能也在 output/ 下
                for fname in ("timing.rpt",):
                    fpath = os.path.join(out_dir, fname)
                    if os.path.exists(fpath) and not any(a.source_uri == fpath for a in artifacts):
                        try:
                            stat = os.stat(fpath)
                            with open(fpath, "rb") as f:
                                chk = hashlib.sha256(f.read()).hexdigest()[:16]
                            artifacts.append(ArtifactInfo(
                                artifact_id=f"art_{uuid.uuid4().hex[:8]}", type="file",
                                logical_name="timing_report", source_uri=fpath, size=stat.st_size,
                                checksum=chk, producer="sta", stage="STA", depends_on=[],
                            ))
                        except (OSError, PermissionError): pass
        # 手动传入的 artifact_manifest
        for a in raw.get("artifact_manifest", []):
            if isinstance(a, ArtifactInfo): artifacts.append(a)
            elif isinstance(a, dict):
                artifacts.append(ArtifactInfo(
                    artifact_id=a.get("artifact_id", f"art_{uuid.uuid4().hex[:8]}"),
                    type=a.get("type","file"), logical_name=a.get("logical_name",""),
                    source_uri=a.get("source_uri",""), size=a.get("size",0),
                    checksum=a.get("checksum",""), producer=a.get("producer",""),
                    stage=a.get("stage",""), depends_on=a.get("depends_on",[]),
                    metadata=a.get("metadata",{}),
                ))
        return artifacts

    # ── ExecutionTrace → TracePoint (v1.0) ──
    def _build_execution_trace(self, raw):
        traces = []
        for t in raw.get("execution_trace", []):
            if isinstance(t, TracePoint): traces.append(t)
            elif isinstance(t, dict):
                traces.append(TracePoint(
                    operation=t.get("operation",""), iteration=t.get("iteration",0),
                    command=t.get("command",""), parameters=t.get("parameters",{}),
                    duration_ms=t.get("duration_ms",0.0), trigger=t.get("trigger",""),
                    metrics_snapshot=t.get("metrics", t.get("metrics_snapshot",{})),
                    checkpoint=t.get("checkpoint",""), timestamp=t.get("timestamp",""),
                ))
        return traces
