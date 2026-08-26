"""导出 OpenAPI 契约到 api/openapi.json，供前端类型生成与漂移检查。

不连接数据库；`uv run python scripts/export_openapi.py` 即可执行。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 保证从任意工作目录运行时都能导入 app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.openapi.utils import get_openapi

from app.main import app


def main() -> None:
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    dest = Path(__file__).resolve().parents[1] / "openapi.json"
    dest.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OpenAPI 已导出: {dest}")


if __name__ == "__main__":
    main()
