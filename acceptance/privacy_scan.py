"""隐私扫描器（TASK-07）：脱敏产物入库前的最后一道人工工具门。

扫描对象（默认 acceptance/reports、acceptance/*.json、web/e2e/fixtures）：
- 通用模式：手机号、身份证号、VIN、中国车牌号；
- 本机探针：samples/probes/*.json 中的真实个人信息字符串（该目录不入
  版本控制；在装有真实样本的机器上，扫描会把这些字符串当作精确探针
  在所有待提交文本中查找——能拦住「拆分文本绕过正则」的泄露形式）。

用法（仓库根目录）：
  uv run python acceptance/privacy_scan.py [路径...]

任一命中即退出码 1；命中内容只打印文件与行号和命中类别，不打印完整
命中原文的上下文（避免把敏感内容复制进日志）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 常见敏感模式（按项目隐私边界：手机号/身份证/VIN/完整车牌）
PATTERNS: dict[str, re.Pattern[str]] = {
    "手机号": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "身份证号": re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:[0-2]\d|3[01])\d{3}[\dXx](?!\d)"),
    "VIN": re.compile(r"(?<![A-Z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Z0-9])"),
    "车牌号": re.compile(
        r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼使领]"
        r"[A-HJ-NP-Z][A-HJ-NP-Z0-9]{4,5}[A-HJ-NP-Z0-9挂学警港澳]"
    ),
}

# 允许出现数字串的例外：测试用的合成数据（例如 E2E fixture 的固定金额）
TEXT_SUFFIXES = {".md", ".json", ".ts", ".tsx", ".py", ".txt"}


def _load_probes() -> list[tuple[str, str]]:
    """从 samples/probes/ 读取真实个人信息字符串（机器上存在时才生效）。"""
    probes: list[tuple[str, str]] = []
    probes_dir = REPO_ROOT / "acceptance" / "samples" / "probes"
    if not probes_dir.exists():
        return probes
    for path in sorted(probes_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for values in data.values():
            for value in values if isinstance(values, list) else [values]:
                if isinstance(value, str) and len(value.strip()) >= 4:
                    probes.append((value.strip(), f"探针({path.name})"))
    return probes


def scan(path: Path, extra_probes: list[tuple[str, str]]) -> list[str]:
    hits: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return hits
    for number, line in enumerate(lines, start=1):
        for label, pattern in PATTERNS.items():
            if pattern.search(line):
                hits.append(f"{path}:{number} 命中[{label}]")
        for probe, label in extra_probes:
            if probe in line:
                hits.append(f"{path}:{number} 命中[{label}]")
    return hits


def main() -> int:
    targets = [Path(arg) for arg in sys.argv[1:]] or [
        REPO_ROOT / "acceptance" / "reports",
        REPO_ROOT / "acceptance" / "manifest.json",
        REPO_ROOT / "acceptance" / "manifest.example.json",
        REPO_ROOT / "acceptance" / "README.md",
        REPO_ROOT / "web" / "e2e" / "fixtures",
        REPO_ROOT / "web" / "e2e" / "assets",
    ]
    extra_probes = _load_probes()
    hits: list[str] = []
    for target in targets:
        if target.is_dir():
            for path in sorted(target.rglob("*")):
                if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
                    hits.extend(scan(path, extra_probes))
        elif target.is_file() and target.suffix.lower() in TEXT_SUFFIXES:
            hits.extend(scan(target, extra_probes))

    if hits:
        print("隐私扫描未通过：")
        for hit in hits:
            print(f"  {hit}")
        return 1
    print("隐私扫描通过：未发现手机号/身份证/VIN/车牌或探针泄露。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
