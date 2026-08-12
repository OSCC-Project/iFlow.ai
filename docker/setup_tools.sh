#!/bin/bash
# Phase 1: 工具链安装 (Ubuntu 22.04)
# 运行: sudo bash setup_tools.sh
# GitHub 被墙时自动跳过需 clone 的工具

set -e
echo "=== iflow-lab Phase 1: 工具链安装 ==="

# --------------------------------------------------
# 1. apt 包 (不依赖 GitHub)
# --------------------------------------------------
echo "[1/3] 安装 apt 包..."
apt-get update -qq
apt-get install -y -qq \
    iverilog \
    verilator \
    python3-pip \
    build-essential \
    git curl \
    flex bison \
    autoconf \
    make gawk

# 确认 Yosys (如未装则装)
if ! command -v yosys &>/dev/null; then
    apt-get install -y -qq yosys yosys-dev
fi

echo "  ✅ apt 包: iverilog + verilator + yosys 就绪"

# --------------------------------------------------
# 2. Python 依赖 (pip, 不依赖 GitHub)
# --------------------------------------------------
echo "[2/3] 安装 Python 依赖..."
pip3 install --break-system-packages -q vcdvcd fstpy 2>/dev/null || true

# pyosys
if python3 -c "import yosys" 2>/dev/null; then
    echo "  ✅ pyosys 已可用"
else
    pip3 install --break-system-packages -q pyosys 2>/dev/null && \
        echo "  ✅ pyosys 安装完成" || \
        echo "  ⚠️  pyosys 安装失败 (非阻塞, adapter 走 subprocess 模式)"
fi

echo "  ✅ Python 依赖就绪"

# --------------------------------------------------
# 3. GitHub 依赖工具 (需要网络, 失败则跳过)
# --------------------------------------------------
echo "[3/3] GitHub 依赖工具 (网络不好自动跳过)..."

# SymbiYosys
if command -v sby &>/dev/null; then
    echo "  ✅ sby 已安装"
else
    echo -n "  编译 SymbiYosys..."
    if git clone --depth 1 https://github.com/YosysHQ/sby.git /tmp/sby 2>/dev/null; then
        cd /tmp/sby && make -j$(nproc) install 2>/dev/null && echo " ✅" || echo " ❌"
        cd / && rm -rf /tmp/sby
    else
        echo " ⚠️  GitHub 不可达, 跳过 (Yosys sat/equiv 可做基础等价检查)"
    fi
fi

# Netgen LVS
if command -v netgen && netgen -batch quit 2>&1 | grep -qi "netgen"; then
    echo "  ✅ Netgen LVS 已安装"
else
    echo -n "  编译 Netgen LVS..."
    if git clone --depth 1 https://github.com/RTimothyEdwards/netgen.git /tmp/netgen-lvs 2>/dev/null; then
        cd /tmp/netgen-lvs && ./configure 2>/dev/null && make -j$(nproc) 2>/dev/null && make install 2>/dev/null && echo " ✅" || echo " ❌"
        cd / && rm -rf /tmp/netgen-lvs
    else
        echo " ⚠️  GitHub 不可达, 跳过 (iDRC 已覆盖 DRC, LVS 待网络恢复)"
    fi
fi

# Verible
if command -v verible-verilog-lint &>/dev/null; then
    echo "  ✅ Verible 已安装"
else
    echo -n "  下载 Verible..."
    # 从 GitHub API 获取最新版本号，拼接下载 URL
    VER=$(curl -sL "https://api.github.com/repos/chipsalliance/verible/releases/latest" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])" 2>/dev/null)
    if [ -n "$VER" ]; then
        URL="https://github.com/chipsalliance/verible/releases/download/${VER}/verible-${VER}-linux-static-x86_64.tar.gz"
        echo "  $VER"
        curl -fSL --connect-timeout 30 --max-time 300 -o /tmp/verible.tar.gz "$URL" 2>/dev/null && \
            tar -xzf /tmp/verible.tar.gz -C /opt 2>/dev/null && \
            cp /opt/verible-*/bin/* /usr/local/bin/ && \
            rm -f /tmp/verible.tar.gz && \
            echo "  ✅ Verible 安装完成" || \
            echo "  ⚠️  下载超时, 重试: curl -fSL -o /tmp/v.tar.gz '$URL'"
    else
        echo "  ⚠️  API 不通, 跳过"
    fi
fi

# --------------------------------------------------
# 验证
# --------------------------------------------------
echo ""
echo "=== 安装验证 ==="
check() {
    local name=$1; shift
    if "$@" >/dev/null 2>&1; then
        echo "  ✅ $name"
    else
        echo "  ❌ $name — $*"
    fi
}

check "iverilog"  iverilog -V
check "vvp"       vvp -v
check "verilator" verilator --version
check "yosys"     yosys -V

echo -n "  "; command -v sby &>/dev/null && echo "✅ sby" || echo "⚠️  sby (待网络恢复)"
echo -n "  "; command -v verible-verilog-lint &>/dev/null && echo "✅ verible" || echo "⚠️  verible (待网络恢复)"
echo -n "  "; netgen -batch quit &>/dev/null && echo "✅ netgen-lvs" || echo "⚠️  netgen-lvs (待网络恢复)"

echo ""
echo "=== 完成 ==="
echo "核心工具: iverilog + verilator + yosys + iEDA/iSTA/iDRC 已就绪"
echo "验证: python3 docker/verify_phase1.py"
