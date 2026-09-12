"""
一键启动全部服务：MCP 共享服务 → 内部 MCP 服务网关 → 网关服务台 → ODM 业务应用。

运行：uv run scripts/start_all_services.py
停止：Ctrl+C（自动终止所有子进程）

启动顺序（三阶段）：
  ① MCP 共享服务（LLM Service / 记忆 / Prompt 中心）
  ② 内部 MCP 服务网关（FastAPI，MCP 调用适配与治理）
  ③ 网关服务台（控制面入口）
  ④ ODM 测试报告 / 问题复盘助手（业务应用）

端口分配：
  9001  LLM Service MCP Server
  9003  记忆服务 MCP Server
  9004  Prompt 中心 MCP Server
  8000  内部 MCP 服务网关 (FastAPI)
  8500  网关服务台
  8502  ODM 测试报告 / 问题复盘助手（后台 /api/* + Web 前端）

端口冲突处理：
  启动前自动检测端口占用，若已占用则终止旧进程后再启动。
  可通过 --no-kill 参数禁用自动杀进程，改为跳过已占用端口。
"""

import os
import platform
import subprocess
import sys
import time
import signal
import socket
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── 启动参数 ─────────────────────────────────────────────

AUTO_KILL = "--no-kill" not in sys.argv

# ── 服务分组定义 ──────────────────────────────────────────

MCP_SERVICES = [
    {
        "name": "LLM Service",
        "cmd": [sys.executable, "shared/llm_gateway/server.py"],
        "port": 9001,
        "group": "mcp",
    },
    {
        "name": "记忆服务",
        "cmd": [sys.executable, "shared/memory_service/server.py"],
        "port": 9003,
        "group": "mcp",
    },
    {
        "name": "Prompt 中心",
        "cmd": [sys.executable, "shared/prompt_hub/server.py"],
        "port": 9004,
        "group": "mcp",
    },
]

GATEWAY_SERVICE = {
    "name": "内部 MCP 服务网关",
    "cmd": [sys.executable, "-m", "uvicorn", "gateway.main:app", "--port", "8000"],
    "port": 8000,
    "group": "gateway",
}

CONSOLE_SERVICE = {
    "name": "网关服务台",
    "cmd": [
        sys.executable, "-m", "uvicorn",
        "projects.gateway_console.web_app:app",
        "--port", "8500",
    ],
    "port": 8500,
    "group": "console",
    "frontend_url": "http://127.0.0.1:8500",
}

PROJECT_SERVICES = [
    {
        "name": "ODM 测试报告 / 问题复盘助手",
        "cmd": [
            sys.executable, "-m", "uvicorn",
            "projects.odm_report_agent.web_app:app",
            "--port", "8502",
        ],
        "port": 8502,
        "group": "project",
        "api_routes": [
            ("POST", "/api/chat", "报告 / 复盘生成"),
            ("POST", "/api/style", "设置输出风格"),
            ("POST", "/api/clear", "清空会话记忆"),
            ("POST", "/api/profile", "查看用户画像"),
        ],
        "frontend_url": "http://127.0.0.1:8502",
    },
]

ALL_SERVICES = MCP_SERVICES + [GATEWAY_SERVICE, CONSOLE_SERVICE] + PROJECT_SERVICES

# ── ANSI 颜色 ────────────────────────────────────────────

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

processes: list[tuple[dict, subprocess.Popen]] = []
shutting_down = False


# ── 工具函数 ──────────────────────────────────────────────

def banner(text: str, char: str = "═", width: int = 60):
    print(f"\n{CYAN}{BOLD}{char * width}")
    print(f"  {text}")
    print(f"{char * width}{RESET}\n")


def check_service_file(service: dict) -> bool:
    cmd = service["cmd"]
    if "-m" in cmd:
        idx = cmd.index("-m") + 1
        module_name = cmd[idx]
        if module_name == "uvicorn" and len(cmd) > idx + 1:
            module_name = cmd[idx + 1]
        module_path = PROJECT_ROOT / module_name.split(":")[0].replace(".", "/")
        return module_path.with_suffix(".py").exists()
    return (PROJECT_ROOT / cmd[1]).exists()


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_for_port(port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_in_use(port):
            return True
        time.sleep(0.3)
    return False


def wait_for_port_free(port: int, timeout: float = 5.0) -> bool:
    """等待端口释放。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not port_in_use(port):
            return True
        time.sleep(0.3)
    return False


# ── 端口占用检测与自动释放 ────────────────────────────────

def find_pid_on_port(port: int) -> list[int]:
    """查找占用指定端口的进程 PID 列表。"""
    pids = []
    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    pid = int(parts[-1])
                    if pid > 0 and pid not in pids:
                        pids.append(pid)
        except Exception:
            pass
    else:
        try:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.append(int(line))
        except Exception:
            pass
    return pids


def get_process_name(pid: int) -> str:
    """获取进程名（用于显示）。"""
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            line = result.stdout.strip()
            if line and not line.startswith("INFO"):
                return line.split(",")[0].strip('"')
        else:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "comm="],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def kill_port_process(port: int) -> bool:
    """终止占用指定端口的进程，返回是否成功释放。"""
    pids = find_pid_on_port(port)
    if not pids:
        return True

    for pid in pids:
        proc_name = get_process_name(pid)
        print(f"    {YELLOW}→ 发现端口 {port} 被 PID={pid} ({proc_name}) 占用，正在终止...{RESET}", flush=True)
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True, timeout=5,
                )
            else:
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.5)
                try:
                    os.kill(pid, 0)
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        except Exception as e:
            print(f"    {RED}→ 无法终止 PID={pid}: {e}{RESET}")
            return False

    if wait_for_port_free(port, timeout=5):
        print(f"    {GREEN}→ 端口 {port} 已释放{RESET}")
        return True

    print(f"    {RED}→ 端口 {port} 释放超时{RESET}")
    return False


def ensure_port_free(port: int, name: str) -> bool:
    """确保端口可用：若占用则自动杀进程或跳过。"""
    if not port_in_use(port):
        return True

    if AUTO_KILL:
        return kill_port_process(port)
    else:
        print(f"  {YELLOW}⚠ {name} 端口 {port} 已被占用，跳过 (使用默认模式可自动释放){RESET}")
        return False


# ── 启动与停止 ────────────────────────────────────────────

def start_service(service: dict, timeout: float = 15.0) -> bool:
    """启动单个服务并等待端口就绪，返回是否成功。"""
    name, cmd, port = service["name"], service["cmd"], service["port"]

    if not ensure_port_free(port, name):
        return False

    print(f"  启动 {BOLD}{name}{RESET} (端口 {port})...", end=" ", flush=True)

    child_env = os.environ.copy()
    child_env["PYTHONUTF8"] = "1"

    popen_kw: dict = dict(cwd=PROJECT_ROOT, env=child_env)

    if platform.system() == "Windows":
        popen_kw["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    else:
        popen_kw["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kw)
    processes.append((service, proc))

    if wait_for_port(port, timeout=timeout):
        print(f"{GREEN}✓ 就绪{RESET}")
        return True

    if proc.poll() is not None:
        print(f"{RED}✗ 启动失败 (exit={proc.returncode}){RESET}")
        return False

    print(f"{YELLOW}⚠ 端口未响应，进程仍在运行{RESET}")
    return True


def shutdown_all():
    """优雅终止所有子进程。"""
    global shutting_down
    if shutting_down:
        return
    shutting_down = True

    print(f"\n{YELLOW}正在停止所有服务...{RESET}")
    for service, proc in reversed(processes):
        if proc.poll() is None:
            proc.terminate()

    for service, proc in reversed(processes):
        name = service["name"]
        try:
            proc.wait(timeout=5)
            print(f"  {GREEN}✓{RESET} {name} 已停止")
        except subprocess.TimeoutExpired:
            proc.kill()
            print(f"  {RED}✗{RESET} {name} 强制终止")

    print(f"\n{GREEN}全部停止。{RESET}")


def on_signal(sig, frame):
    shutdown_all()
    sys.exit(0)


def check_env_file():
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        print(f"  {RED}✗ 未找到 .env 文件{RESET}")
        print(f"    请先执行: cp .env.example .env 并填入豆包 API Key")
        return False
    return True


def scan_ports():
    """预扫描所有端口状态。"""
    occupied = []
    for s in ALL_SERVICES:
        if port_in_use(s["port"]):
            pids = find_pid_on_port(s["port"])
            names = [get_process_name(p) for p in pids] if pids else ["unknown"]
            occupied.append((s, pids, names))
    return occupied


# ── 主流程 ────────────────────────────────────────────────

def main():
    banner("MCP 全栈服务启动器")

    # ── 环境检查 ──
    print(f"{BOLD}[环境检查]{RESET}")
    if not check_env_file():
        sys.exit(1)
    print(f"  {GREEN}✓{RESET} .env 配置文件存在")

    missing = [s for s in ALL_SERVICES if not check_service_file(s)]
    available = [s for s in ALL_SERVICES if check_service_file(s)]

    if not available:
        print(f"\n{RED}没有找到任何可启动的服务，请先完成各模块开发。{RESET}")
        sys.exit(1)

    if missing:
        print(f"\n  {YELLOW}以下服务文件缺失，将跳过：{RESET}")
        for s in missing:
            print(f"    ⏭  {s['name']} (端口 {s['port']})")

    print(f"  {GREEN}✓{RESET} 将启动 {len(available)} 个服务")

    # ── 端口状态预扫描 ──
    print(f"\n{BOLD}[端口状态扫描]{RESET}")
    occupied = scan_ports()
    if not occupied:
        print(f"  {GREEN}✓{RESET} 所有端口均空闲")
    else:
        for s, pids, names in occupied:
            pid_str = ", ".join(f"PID={p}({n})" for p, n in zip(pids, names))
            print(f"  {YELLOW}●{RESET} 端口 {s['port']} ({s['name']}) 已占用 → {pid_str}")
        if AUTO_KILL:
            print(f"  {CYAN}→ 将自动终止占用进程后启动{RESET}")
        else:
            print(f"  {YELLOW}→ --no-kill 模式，已占用端口将跳过{RESET}")

    print()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    # ── 第一阶段：MCP 共享服务 ──
    mcp_available = [s for s in MCP_SERVICES if s in available]
    if mcp_available:
        banner("第 1 阶段 — MCP 共享服务", "─", 50)
        for service in mcp_available:
            if not start_service(service):
                print(f"\n{RED}关键服务启动失败，正在回滚...{RESET}")
                shutdown_all()
                sys.exit(1)

    # ── 第二阶段：内部 MCP 服务网关 ──
    if GATEWAY_SERVICE in available:
        banner("第 2 阶段 — 内部 MCP 服务网关", "─", 50)
        if not start_service(GATEWAY_SERVICE):
            print(f"\n{RED}网关启动失败，正在回滚...{RESET}")
            shutdown_all()
            sys.exit(1)

    # ── 第三阶段：网关服务台 ──
    if CONSOLE_SERVICE in available:
        banner("第 3 阶段 — 网关服务台", "─", 50)
        if not start_service(CONSOLE_SERVICE, timeout=10):
            print(f"  {YELLOW}⚠ {CONSOLE_SERVICE['name']} 未能正常启动，其他服务继续运行{RESET}")

    # ── 第四阶段：业务项目 ──
    proj_available = [s for s in PROJECT_SERVICES if s in available]
    if proj_available:
        banner("第 4 阶段 — 业务项目（后台 API + Web 前端）", "─", 50)
        for service in proj_available:
            if not start_service(service, timeout=10):
                print(f"  {YELLOW}⚠ {service['name']} 未能正常启动，其他服务继续运行{RESET}")

    # ── 启动完成 — 汇总面板 ──
    banner("全部服务已就绪")

    print(f"  {BOLD}{CYAN}[MCP 共享服务]{RESET}")
    for s in mcp_available:
        status = f"{GREEN}●{RESET}" if port_in_use(s["port"]) else f"{RED}○{RESET}"
        print(f"    {status} {s['name']:12s}  http://127.0.0.1:{s['port']}")

    if GATEWAY_SERVICE in available:
        print(f"\n  {BOLD}{CYAN}[内部 MCP 服务网关]{RESET}")
        gw = GATEWAY_SERVICE
        status = f"{GREEN}●{RESET}" if port_in_use(gw["port"]) else f"{RED}○{RESET}"
        print(f"    {status} {gw['name']:12s}  http://127.0.0.1:{gw['port']}")

    if CONSOLE_SERVICE in available:
        print(f"\n  {BOLD}{CYAN}[网关服务台]{RESET}")
        status = f"{GREEN}●{RESET}" if port_in_use(CONSOLE_SERVICE["port"]) else f"{RED}○{RESET}"
        print(f"    {status} {CONSOLE_SERVICE['name']:12s}  {CONSOLE_SERVICE['frontend_url']}")

    if proj_available:
        print(f"\n  {BOLD}{CYAN}[业务项目]{RESET}")
        for s in proj_available:
            status = f"{GREEN}●{RESET}" if port_in_use(s["port"]) else f"{RED}○{RESET}"
            url = s.get("frontend_url", f"http://127.0.0.1:{s['port']}")
            print(f"    {status} {s['name']}")
            print(f"      {BOLD}Web 前端:{RESET}  {url}")
            api_routes = s.get("api_routes", [])
            if api_routes:
                print(f"      {BOLD}后台 API:{RESET}")
                for method, path, desc in api_routes:
                    print(f"        {DIM}{method:6s}{RESET} {url}{path}  {DIM}— {desc}{RESET}")

    print(f"\n  {DIM}按 Ctrl+C 停止所有服务{RESET}\n")
    print(f"{'─' * 60}")

    # ── 进程监控 ──
    try:
        reported_dead = set()
        while True:
            for service, proc in processes:
                if proc.poll() is not None and id(proc) not in reported_dead:
                    reported_dead.add(id(proc))
                    name = service["name"]
                    code = proc.returncode
                    if code != 0:
                        print(f"\n  {RED}⚠ {name} 进程已退出 (exit={code})，请查看对应窗口输出{RESET}")
            if len(reported_dead) == len(processes):
                print(f"\n{RED}所有服务已退出。{RESET}")
                break
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_all()


if __name__ == "__main__":
    main()
