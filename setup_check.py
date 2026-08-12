#!/usr/bin/env python3
"""
setup_check.py — 环境就绪检查（别人拿到项目后第一步运行）

$ python3 setup_check.py

检查项:
  - Python ≥ 3.10
  - Yosys 已安装
  - OpenROAD 已安装
  - OpenSTA 存在
  - 内置 RTL 文件齐全 (rtl/gcd.v, rtl/aes_cipher_top.v, rtl/uart.v)
  - Python 依赖 (yaml, jinja2)
  - PDK (sky130) 配置正确

全部通过后才能跑 python3 cli.py
"""
import os, sys, shutil

PASS, FAIL = 0, 0

def check(label, condition, detail=""):
    global PASS, FAIL
    if condition: PASS += 1; print(f"  ✅ {label:30s} {detail}")
    else: FAIL += 1; print(f"  ❌ {label:30s} {detail}")

print("=" * 55)
print("  IC-Agent-OS  环境检查")
print("=" * 55)

BASE = os.path.dirname(os.path.abspath(__file__))

# Python
check("Python ≥ 3.10", sys.version_info >= (3, 10), sys.version.split()[0])

# Yosys
y = shutil.which("yosys")
check("Yosys", bool(y), y or "未安装 → apt install yosys")

# OpenROAD
o = shutil.which("openroad")
check("OpenROAD", bool(o), o or "未安装")

# OpenSTA
s = shutil.which("sta")
check("OpenSTA (sta)", bool(s), s or "未安装(非必需)")

# RTL files
for name, path in [("gcd", "rtl/gcd.v"), ("aes", "rtl/aes_cipher_top.v"), ("uart", "rtl/uart.v"), ("picorv32", "rtl/picorv32.v")]:
    ok = os.path.exists(os.path.join(BASE, path))
    check(f"RTL: {name}", ok, path if ok else f"缺失: {path}")

# Python packages
for pkg in ["yaml", "jinja2"]:
    try:
        __import__(pkg); check(f"pip: {pkg}", True)
    except ImportError:
        check(f"pip: {pkg}", False, f"pip install {pkg}")

# PDK — 从 config.yaml 读取路径 (不硬编码)
try:
    import yaml
    cfg = yaml.safe_load(open(os.path.join(BASE, "adapter/config.yaml")))
    pdk = cfg.get("backend", {}).get("openroad", {}).get("pdk", {})
    pdk_dir = os.path.dirname(pdk.get("tech_lef", "")) or os.path.dirname(pdk.get("liberty", ""))
    pdk_files = {
        "tech LEF": pdk.get("tech_lef", ""),
        "cell LEF": pdk.get("cell_lef", ""),
        "liberty (TYP)": pdk.get("liberty", ""),
        "tracks": pdk.get("tracks", ""),
        "vars": pdk.get("vars", ""),
    }
    corners = cfg.get("backend", {}).get("openroad", {}).get("corners", {})
    for c in ["SLOW", "FAST"]:
        if c in corners:
            pdk_files[f"liberty ({c})"] = corners[c].get("liberty", "")
    pdk_ok = True
    for name, path in pdk_files.items():
        exists = bool(path and os.path.exists(path))
        if not exists: pdk_ok = False
        check(f"PDK: {name}", exists, path or "未配置")
    if not pdk_dir:
        check("PDK (sky130)", False, "config.yaml 中未配置 PDK 路径")
except Exception as e:
    check("PDK (sky130)", False, f"读取 config.yaml 失败: {e}")

print(f"\n{'─'*55}")
if FAIL == 0:
    print(f"  ✅ 全部 {PASS} 项通过! 可以运行: python3 cli.py")
else:
    print(f"  ⚠️  {FAIL}/{PASS+FAIL} 项失败, 请先修复")
    if FAIL > 0:
        print(f"  提示: 编辑 adapter/config.yaml → backend.openroad.pdk 设置 PDK 路径")
print(f"{'─'*55}")
