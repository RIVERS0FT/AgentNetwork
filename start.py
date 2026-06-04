#!/usr/bin/env python3
"""
AI Agent 仿真运行平台 —— 一键启动脚本
自动检查环境 → 安装依赖 → 启动后端+前端 → 打开浏览器

用法:
    python start.py              # 默认 http://localhost:8000
    python start.py --port 9000  # 自定义端口
    python start.py --no-browser # 不自动打开浏览器
    python start.py --host 0.0.0.0 --port 8000  # 允许外部访问
"""

import sys
import os
import subprocess
import time
import argparse
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
os.chdir(ROOT)

# ── 终端颜色 ───────────────────────────────────────────
class Style:
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"

def ok(msg):    print(f"  {Style.GREEN}✓{Style.RESET} {msg}")
def warn(msg):  print(f"  {Style.YELLOW}⚠{Style.RESET} {msg}")
def fail(msg):  print(f"  {Style.RED}✗{Style.RESET} {msg}")
def info(msg):  print(f"  {Style.CYAN}→{Style.RESET} {msg}")
def title(msg): print(f"\n{Style.BOLD}{'━'*60}\n  {msg}\n{'━'*60}{Style.RESET}\n")


# ── 步骤 1: 检查 Python 版本 ──────────────────────────
def check_python() -> bool:
    title("Step 1/4  Python 环境")
    v = sys.version_info
    print(f"  Python {v.major}.{v.minor}.{v.micro}  [{sys.executable}]")
    if v < (3, 10):
        fail("需要 Python >= 3.10")
        return False
    ok("Python 版本符合要求")
    return True


# ── 步骤 2: 检查并安装依赖 ────────────────────────────
REQUIRED = [
    "fastapi",
    "uvicorn",
    "pydantic",
    "aiofiles",          # FastAPI StaticFiles 可选但推荐
    "anthropic",         # LLM 决策（可选，brain.py 有降级）
    "prometheus_client",  # /metrics 端点
    "requests",          # message_bus 通信
]

def install_deps() -> bool:
    title("Step 2/4  依赖检查")

    missing = []
    for pkg in REQUIRED:
        try:
            __import__(pkg.replace("-", "_"))
        except ImportError:
            missing.append(pkg)

    if not missing:
        ok("所有核心依赖已安装")
        return True

    warn(f"缺少 {len(missing)} 个包: {', '.join(missing)}")
    info("正在安装...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", *missing,
             "-q", "--disable-pip-version-check"],
            timeout=120,
        )
        ok("依赖安装完成")
        return True
    except Exception as e:
        fail(f"安装失败: {e}")
        print("  请手动执行: pip install " + " ".join(missing))
        return False


# ── 步骤 3: 可选服务检查 ──────────────────────────────
def check_optional() -> None:
    title("Step 3/4  可选服务检查")

    # Elasticsearch
    try:
        from agent_network.es_client import ESClient
        es = ESClient()
        if es.enabled:
            ok("Elasticsearch — 已连接")
        else:
            warn("Elasticsearch — 未连接（日志/搜索将使用内存模式）")
    except Exception:
        warn("Elasticsearch — 不可用")


# ── 步骤 4: 启动服务 ──────────────────────────────────
def start_server(host: str, port: int, reload: bool, no_browser: bool) -> None:
    title("Step 4/4  启动服务")

    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"

    print(f"""
  ╔══════════════════════════════════════════════════════════╗
  ║   AI Agent 仿真运行平台                                  ║
  ╠══════════════════════════════════════════════════════════╣
  ║  控制台:  {url:<48s}║
  ║  API 文档: {url+'/docs':<48s}║
  ║  Metrics: {url+'/metrics':<48s}║
  ╚══════════════════════════════════════════════════════════╝
""")

    if not no_browser:
        info(f"即将在浏览器打开控制台...")
        time.sleep(1.5)
        webbrowser.open(url)

    # 启动 uvicorn
    import uvicorn
    uvicorn.run(
        "server:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


# ── 入口 ───────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="AI Agent 仿真运行平台 — 一键启动",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python start.py                     # 默认 127.0.0.1:8000
  python start.py --port 3000         # 自定义端口
  python start.py --host 0.0.0.0     # 允许局域网访问
  python start.py --reload            # 开发模式（代码变更自动重载）
  python start.py --no-browser        # 不自动打开浏览器
        """,
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址 (默认 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="端口 (默认 8000)")
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    print(f"\n{Style.BOLD}{Style.CYAN}")
    print("    ╔══════════════════════════════════╗")
    print("    ║  AI Agent 仿真运行平台 启动器    ║")
    print("    ╚══════════════════════════════════╝")
    print(Style.RESET)

    # 环境变量
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    sys.path.insert(0, str(ROOT))

    # 执行检查
    if not check_python():
        sys.exit(1)
    if not install_deps():
        sys.exit(1)
    check_optional()

    # 启动
    start_server(args.host, args.port, args.reload, args.no_browser)


if __name__ == "__main__":
    main()
