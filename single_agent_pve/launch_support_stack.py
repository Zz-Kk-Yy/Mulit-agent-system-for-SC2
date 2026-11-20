"""
单终端拉起：Redis(Docker) + Ask API（uvicorn）。

- 仪表盘 JSON → Redis 的同步已并入 `swarmbrain_launch.py` 后台线程，无需单独开 bridge。
- 大模型 Key：在项目根 `.env` 中配置 `SWARMBRAIN_LLM_API_KEY`（与星际主流程相同），或由父进程导出环境变量；子进程继承。

另开终端运行对局：`python swarmbrain_launch.py`。

用法:
  python launch_support_stack.py
  python launch_support_stack.py --skip-docker
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def _wait_tcp(host: str, port: int, timeout: float = 45.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.5):
                return True
        except OSError:
            time.sleep(0.4)
    return False


def _tcp_listening(host: str, port: int, connect_timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=connect_timeout):
            return True
    except OSError:
        return False


def _which(name: str) -> str | None:
    return shutil.which(name)


def _docker_compose_cmd() -> list[str] | None:
    if _which("docker") and subprocess.run(
        ["docker", "compose", "version"],
        cwd=ROOT,
        capture_output=True,
    ).returncode == 0:
        return ["docker", "compose"]
    if _which("docker-compose"):
        return ["docker-compose"]
    return None


def _start_docker_redis() -> None:
    host = os.getenv("REDIS_HOST", "127.0.0.1")
    port = int(os.getenv("REDIS_PORT", "6379"))

    if _tcp_listening(host, port):
        print(
            f"[stack] {host}:{port} 已有服务监听，跳过 docker compose（复用现有 Redis）。",
            flush=True,
        )
        return

    dc = _docker_compose_cmd()
    if not dc:
        print("[stack] 未找到 docker compose，跳过容器启动（请自行保证 Redis 可用）")
        return
    r = subprocess.run([*dc, "up", "-d"], cwd=str(ROOT))
    if r.returncode == 0:
        if _wait_tcp(host, port, timeout=30.0):
            print("[stack] Redis 容器已就绪", flush=True)
        else:
            print("[stack] Docker 已成功但 Redis 端口暂不可用，Ask API 可能回退到本地 JSON …", flush=True)
        return

    if _tcp_listening(host, port):
        print(
            "[stack] docker compose 未能绑定端口，但 Redis 仍可用，继续启动 Ask API。",
            flush=True,
        )
        return

    print(
        "[stack] docker compose 失败且无法连接 Redis；请检查 Docker 或设置 --skip-docker。",
        flush=True,
    )


def _pump(prefix: str, proc: subprocess.Popen[str]) -> None:
    if proc.stdout is None:
        return
    try:
        for line in proc.stdout:
            print(f"[{prefix}] {line}", end="")
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="启动 Redis(Docker) 与问答 API")
    parser.add_argument("--skip-docker", action="store_true", help="不运行 docker compose，使用已有 Redis")
    parser.add_argument(
        "--ask-host",
        default=os.getenv("SWARMBRAIN_ASK_HOST", "127.0.0.1"),
        help="Ask API bind host（默认 127.0.0.1）",
    )
    parser.add_argument(
        "--ask-port",
        type=int,
        default=int(os.getenv("SWARMBRAIN_ASK_PORT", "8011")),
        help="Ask API 端口（默认 8011）",
    )
    args = parser.parse_args()
    py = sys.executable

    if not args.skip_docker:
        _start_docker_redis()

    redis_host = os.getenv("REDIS_HOST", "127.0.0.1")
    if not _wait_tcp(redis_host, int(os.getenv("REDIS_PORT", "6379")), timeout=5.0):
        print(
            f"[stack] 当前无法连接 Redis {redis_host}:{os.getenv('REDIS_PORT', '6379')}，"
            "Ask API 将尽量使用 dashboard_state.json；对局时请确保已在本机启动 `swarmbrain_launch`（内含 Redis 同步线程）。",
            flush=True,
        )

    if _tcp_listening(args.ask_host, args.ask_port):
        print(
            f"[stack] Ask API 端口 {args.ask_host}:{args.ask_port} 已被占用。\n"
            "可结束占用进程或使用： python launch_support_stack.py --ask-port 8012",
            flush=True,
        )
        return 1

    llm_warn = True
    try:
        from swarmbrain_llm_shared import get_swarmbrain_llm_credentials

        llm_warn = not bool(str(get_swarmbrain_llm_credentials()["api_key"]).strip())
    except Exception:
        llm_warn = not any(
            os.getenv(n, "").strip()
            for n in ("SWARMBRAIN_LLM_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY")
        )
    if llm_warn:
        print(
            "[stack] 提示：未取得任何 LLM API Key（与 swarmbrain_llm_shared / .env 均无关时），`/api/ask` 仅能走兜底规则。\n",
            flush=True,
        )

    argv = [
        py,
        "-m",
        "uvicorn",
        "ask_api_server:app",
        "--host",
        args.ask_host,
        "--port",
        str(args.ask_port),
    ]
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")

    proc = subprocess.Popen(
        argv,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    stop_state = {"done": False}

    def cleanup(*_a: object) -> None:
        if stop_state["done"]:
            return
        stop_state["done"] = True
        print("\n[stack] 正在结束 Ask API …", flush=True)
        if proc.poll() is None:
            proc.terminate()
            deadline = time.monotonic() + 8
            while proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            if proc.poll() is None:
                proc.kill()

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    threading.Thread(target=_pump, args=("ask-api", proc), daemon=True).start()

    print(
        f"[stack] Ask API: http://{args.ask_host}:{args.ask_port}/api/ask  健康检查: /health\n"
        "[stack] 对局与 Redis 同步：另开终端运行 python swarmbrain_launch.py\n"
        "[stack] 按 Ctrl+C 结束。\n",
        flush=True,
    )

    try:
        rc = proc.wait()
        return rc if rc is not None else 0
    except KeyboardInterrupt:
        cleanup()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
