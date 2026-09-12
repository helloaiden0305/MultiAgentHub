"""
一键启动所有 MCP 共享服务 + HTTP 网关。
按顺序拉起 3 个 MCP Server，等待就绪后再启动网关。

运行：uv run scripts/start_all.py
停止：Ctrl+C（会自动终止所有子进程）
"""

import subprocess
import sys
import time
import signal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SERVICES = [
    {
        "name": "LLM 网关",
        "cmd": [sys.executable, "shared/llm_gateway/server.py"],
        "port": 9001,
    },
    {
        "name": "记忆服务",
        "cmd": [sys.executable, "shared/memory_service/server.py"],
        "port": 9003,
    },
    {
        "name": "Prompt 中心",
        "cmd": [sys.executable, "shared/prompt_hub/server.py"],
        "port": 9004,
    },
    {
        "name": "HTTP 网关",
        "cmd": [sys.executable, "-m", "uvicorn", "gateway.main:app", "--port", "8000"],
        "port": 8000,
    },
]

processes: list[subprocess.Popen] = []


def check_service_file(service: dict) -> bool:
    """检查服务入口文件是否存在。"""
    cmd = service["cmd"]
    if cmd[1] == "-m":
        module_path = PROJECT_ROOT / cmd[3].split(":")[0].replace(".", "/")
        return module_path.with_suffix(".py").exists()
    return (PROJECT_ROOT / cmd[1]).exists()


def wait_for_port(port: int, timeout: float = 10.0) -> bool:
    """等待端口可连接，返回是否成功。"""
    import socket

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def shutdown_all():
    """终止所有已启动的子进程。"""
    for proc in reversed(processes):
        if proc.poll() is None:
            proc.terminate()
    for proc in reversed(processes):
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def main():
    missing = [s for s in SERVICES if not check_service_file(s)]
    available = [s for s in SERVICES if check_service_file(s)]

    if not available:
        print("没有找到任何可启动的服务，请先完成各 Step 的开发。")
        print("可启动的脚本参考：")
        for s in SERVICES:
            print(f"  {s['name']:12s}  端口 {s['port']}  {' '.join(s['cmd'][1:])}")
        sys.exit(1)

    if missing:
        print("以下服务尚未实现，将跳过：")
        for s in missing:
            print(f"  ⏭ {s['name']} (端口 {s['port']})")
        print()

    def on_signal(sig, frame):
        print("\n正在停止所有服务...")
        shutdown_all()
        sys.exit(0)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    for service in available:
        name, cmd, port = service["name"], service["cmd"], service["port"]
        print(f"启动 {name} (端口 {port})...")

        proc = subprocess.Popen(
            cmd,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        processes.append(proc)

        if wait_for_port(port, timeout=15):
            print(f"  ✓ {name} 已就绪")
        else:
            if proc.poll() is not None:
                output = proc.stdout.read() if proc.stdout else ""
                print(f"  ✗ {name} 启动失败 (exit={proc.returncode})")
                if output:
                    for line in output.strip().splitlines()[-5:]:
                        print(f"    {line}")
                print("\n正在停止已启动的服务...")
                shutdown_all()
                sys.exit(1)
            else:
                print(f"  ⚠ {name} 端口 {port} 未响应，但进程仍在运行，继续...")

    print()
    print("=" * 50)
    print("所有服务已启动:")
    for s in available:
        print(f"  {s['name']:12s}  http://127.0.0.1:{s['port']}")
    print("=" * 50)
    print("按 Ctrl+C 停止所有服务")
    print()

    try:
        while True:
            for proc in processes:
                if proc.poll() is not None:
                    idx = processes.index(proc)
                    name = available[idx]["name"]
                    print(f"⚠ {name} 进程已退出 (exit={proc.returncode})")
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n正在停止所有服务...")
        shutdown_all()
        print("已全部停止。")


if __name__ == "__main__":
    main()
