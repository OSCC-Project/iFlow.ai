"""对比实验 区域 C: 空间 Map 横向对比 (方案 5.2.3 + aes11 第 5 节)

数据源: 布线后 DEF 的 COMPONENTS 坐标 → 180×180 网格密度直方图 → matplotlib PNG。
统一色标: 同一实验的所有组合共用同一个 vmax, 颜色可横向比较
(全零/无数据图如实标注, 不拉伸成伪热点 — 与参考报告口径一致)。
"""
import os, re
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

GRID_N = 180
MAPS_DIR = "/tmp/iflow_workspace/maps"


def def_density_grid(def_path: str, n: int = GRID_N):
    """解析 DEF COMPONENTS 坐标 → (密度网格, bbox)"""
    try:
        with open(def_path, errors="replace") as f:
            content = f.read()
    except OSError:
        return None
    pts = []
    for m in re.finditer(r'PLACED\s*\(\s*(-?[\d.]+)\s+(-?[\d.]+)\s*\)', content):
        pts.append((float(m.group(1)), float(m.group(2))))
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    grid = np.zeros((n, n))
    for x, y in pts:
        gx = int((x - x0) / max(x1 - x0, 1e-9) * (n - 1))
        gy = int((y - y0) / max(y1 - y0, 1e-9) * (n - 1))
        grid[gy, gx] += 1
    return grid, (x0, y0, x1, y1)


def render_density_png(def_path: str, out_png: str, title: str, vmax: float) -> bool:
    """渲染单张密度热力图 (统一 vmax 色标), 成功返回 True"""
    r = def_density_grid(def_path)
    if r is None:
        return False
    grid, _ = r
    fig, ax = plt.subplots(figsize=(3.2, 3.2), dpi=100)
    im = ax.imshow(grid, origin="lower", cmap="inferno", vmin=0,
                   vmax=max(vmax, 1e-9), aspect="equal")
    ax.set_title(title, fontsize=7, color="#333")
    ax.axis("off")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    plt.tight_layout()
    fig.savefig(out_png, facecolor="white")
    plt.close(fig)
    return True


def find_def_path(combo_result: dict) -> str:
    """从组合运行结果中定位布线后 DEF (iEDA: iRT_result.def / OpenROAD: route.def)"""
    for s in combo_result.get("results", []):
        if s["step"] == "openroad_physical" and s.get("def_path"):
            if os.path.exists(s["def_path"]):
                return s["def_path"]
        if s["step"] == "ieda_route" and s.get("run_dir"):
            cand = os.path.join(s["run_dir"], "result", "iRT_result.def")
            if os.path.exists(cand):
                return cand
    return ""


def render_experiment_maps(exp: dict) -> dict:
    """为实验的全部组合渲染密度 Map (统一色标), 返回 {maps: [...], vmax}"""
    os.makedirs(MAPS_DIR, exist_ok=True)
    exp_dir = os.path.join(MAPS_DIR, exp["id"])
    os.makedirs(exp_dir, exist_ok=True)

    # 第一遍: 找所有 DEF + 全局最大密度 (统一色标)
    entries = []
    for r in exp.get("results", []):
        if "result" not in r:
            continue
        def_path = find_def_path(r["result"])
        if not def_path:
            entries.append({"combo_id": r["combo_id"], "config": r["config"],
                            "png": "", "def_path": ""})
            continue
        entries.append({"combo_id": r["combo_id"], "config": r["config"],
                        "png": "", "def_path": def_path})
    vmax = 0.0
    for e in entries:
        if e["def_path"]:
            g = def_density_grid(e["def_path"])
            if g is not None:
                vmax = max(vmax, float(g[0].max()))

    # 第二遍: 渲染 (统一 vmax)
    for e in entries:
        if not e["def_path"]:
            continue
        png = os.path.join(exp_dir, f"{e['combo_id']}_density.png")
        title = " / ".join(f"{k}={v}" for k, v in e["config"].items()
                           if not str(k).startswith("_"))
        if render_density_png(e["def_path"], png, title, vmax):
            e["png"] = png
    return {"maps": entries, "vmax": round(vmax, 1), "grid": GRID_N}
