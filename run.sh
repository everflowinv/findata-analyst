#!/usr/bin/env bash
# run.sh - Auto-bootstrapping wrapper for findata-analyst

# 1. 跨平台读取环境变量 (兼容 Mac 和 Linux)
# 临时关闭报错退出，并屏蔽输出，防止 Bash 读取 Zsh 专有语法时崩溃
set +e
[ -f ~/.bash_profile ] && source ~/.bash_profile >/dev/null 2>&1
[ -f ~/.bashrc ] && source ~/.bashrc >/dev/null 2>&1
[ -f ~/.zprofile ] && source ~/.zprofile >/dev/null 2>&1
[ -f ~/.zshrc ] && source ~/.zshrc >/dev/null 2>&1
set -e

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SKILL_DIR/venv"
REQ_FILE="$SKILL_DIR/requirements.txt"
SCRIPT_FILE="$SKILL_DIR/scripts/findata_cli.py"

# 2. 跨平台动态扫描：支持 Mac (Homebrew) 和 Linux (apt/yum/dnf)
get_best_python() {
    # 路径优先级阵列：
    # 1. Mac M芯片 Homebrew (/opt/homebrew/bin)
    # 2. Mac Intel 或 Linux 手动编译版 (/usr/local/bin)
    # 3. Linux 系统包管理器默认路径 (/usr/bin)
    # 4. Linux 用户级无 root 安装路径 ($HOME/.local/bin)
    for prefix in "/opt/homebrew/bin" "/usr/local/bin" "/usr/bin" "$HOME/.local/bin"; do
        if [ -d "$prefix" ]; then
            # 查找所有形如 python3.X 的文件，按版本号大小自然排序，抓取最高版本
            local best_py=$(ls -1 "$prefix"/python3.* 2>/dev/null | grep -E "^$prefix/python3\.[0-9]+$" | sort -V | tail -n 1)
            
            # 只要找到了且能运行，就立刻返回它并终止扫描
            if [ -n "$best_py" ] && [ -x "$best_py" ]; then
                echo "$best_py"
                return 0
            fi
        fi
    done
    
    # 极端兜底选项
    echo "python3"
}

# 自动化装配 (Auto-bootstrap)
if [ ! -d "$VENV_DIR" ]; then
    BASE_PYTHON=$(get_best_python)
    echo "Initializing virtual environment using: $BASE_PYTHON" >&2
    
    "$BASE_PYTHON" -m venv "$VENV_DIR"
    
    echo "Installing dependencies..." >&2
    "$VENV_DIR/bin/pip" install --upgrade pip --quiet
    "$VENV_DIR/bin/pip" install -r "$REQ_FILE" --quiet
    echo "Environment ready." >&2
fi

# 3. 使用专属虚拟环境执行 Python 脚本，并加入 -W ignore 屏蔽警告
exec "$VENV_DIR/bin/python" -W ignore "$SCRIPT_FILE" "$@"
