"""将前后端、Ask API、Redis/MCP/DeerFlow 流水线所需文件复制到 dist/ 并打 zip 至 docs/。"""
from __future__ import annotations

import shutil
import zipfile
from datetime import datetime
from pathlib import Path

# scripts/ 下的工具：仓库根目录为上一级
REPO_ROOT = Path(__file__).resolve().parents[1]
# 默认打包「单智能体 PvE」可运行包（与历史 dist 行为一致）
PKG_SRC = REPO_ROOT / "single_agent_pve"
DIST = REPO_ROOT / "dist"
DOCS = REPO_ROOT / "docs" / "packaging"
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
STAGE = DIST / f"SwarmBrain_deploy_{STAMP}"
ZIP_NAME = f"SwarmBrain_deploy_{STAMP}.zip"
ZIP_PATH = DOCS / ZIP_NAME

_COPY_IGNORE = shutil.ignore_patterns(
    "__pycache__",
    "*.pyc",
    ".DS_Store",
    ".venv",
    "videos",
    "logs",
)


def main() -> None:
    if not PKG_SRC.is_dir():
        raise SystemExit(f"缺少模式目录: {PKG_SRC}")

    if STAGE.exists():
        shutil.rmtree(STAGE)
    shutil.copytree(PKG_SRC, STAGE, ignore=_COPY_IGNORE)

    # 初始 dashboard_state.json（若缺失）
    state_path = STAGE / "dashboard_state.json"
    if not state_path.is_file():
        import sys

        sys.path.insert(0, str(STAGE))
        from dashboard_state import DashboardStateWriter  # noqa: E402

        DashboardStateWriter(STAGE, state_path)

    DOCS.mkdir(parents=True, exist_ok=True)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in STAGE.rglob("*"):
            if path.is_file():
                # 不把真实 .env 打进公开包
                if path.name == ".env":
                    continue
                arc = path.relative_to(STAGE)
                zf.write(path, arc.as_posix())

    print(f"已生成暂存目录: {STAGE}")
    print(f"已生成部署压缩包: {ZIP_PATH}")
    print(f"压缩包大小: {ZIP_PATH.stat().st_size / 1024 / 1024:.2f} MB")
    print("提示: 多智能体包请直接使用/分发 multi_agent_duel/ 目录。")


if __name__ == "__main__":
    main()
