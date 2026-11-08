#!/usr/bin/env python3
"""公开前脱敏扫描：这个仓库里不能出现本体痕迹、机器信息、凭据。

用法：
    python3 scripts/desensitize_scan.py            # 扫整个仓库
    python3 scripts/desensitize_scan.py --json     # 机器可读
    python3 scripts/desensitize_scan.py path...    # 只扫指定文件

判据：任一命中即失败（exit 1）。宁可误报，不可漏报——这是公开前的最后一道闸。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TEXT_EXT = {".md", ".py", ".ts", ".tsx", ".js", ".json", ".yml", ".yaml", ".toml",
            ".swift", ".sh", ".html", ".css", ".txt", ".cfg", ".ini", ""}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".next"}
SKIP_NAMES = {".DS_Store"}

# 本体与机器痕迹：按"绝不允许出现"处理
RULES: list[tuple[str, str, str]] = [
    ("private_root",   r"AdidiCore|adidios|ADIDI_[A-Z_]+", "本体私有命名"),
    ("user_path",      r"/Users/[A-Za-z0-9._-]+|~/Adidi\w*|/Volumes/[^\s\"']+", "绝对路径/挂载盘"),
    ("machine_user",   r"mr\.czj", "机器用户名"),
    ("cn_name",        r"阿弟|哥哥", "私人称谓"),
    ("email",          r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "邮箱"),
    ("openai_key",     r"sk-[A-Za-z0-9_-]{16,}", "疑似 API key"),
    ("github_token",   r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}", "GitHub token"),
    ("aws_key",        r"AKIA[0-9A-Z]{16}", "AWS key"),
    ("private_key",    r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私钥"),
    ("bearer",         r"Bearer\s+[A-Za-z0-9._-]{20,}", "Bearer token"),
    ("telegram_token", r"\b\d{8,10}:AA[A-Za-z0-9_-]{30,}", "Telegram bot token"),
    ("lan_ip",         r"\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b", "内网地址"),
]

# 规则文件本身必须写出这些模式文本，否则无法检测；只豁免它自己
WHITELIST_FILES = {"scripts/desensitize_scan.py"}


def allowed(rel: str, name: str, hit: str) -> bool:
    return rel.replace(os.sep, "/") in WHITELIST_FILES


def iter_files(targets: list[Path]):
    for target in targets:
        if target.is_file():
            yield target
            continue
        for dirpath, dirnames, filenames in os.walk(target):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                if name in SKIP_NAMES or name.endswith(".pyc"):
                    continue
                path = Path(dirpath) / name
                if path.suffix.lower() not in TEXT_EXT:
                    continue
                yield path


def scan(targets: list[Path]) -> list[dict]:
    findings: list[dict] = []
    for path in iter_files(targets):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = str(path.relative_to(ROOT)) if ROOT in path.parents or path.parent == ROOT else str(path)
        for rule, pattern, label in RULES:
            for match in re.finditer(pattern, text):
                hit = match.group(0)
                if allowed(rel, rule, hit):
                    continue
                line = text.count("\n", 0, match.start()) + 1
                findings.append({"file": rel, "line": line, "rule": rule,
                                 "label": label, "match": hit[:80]})
    return findings


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv
    targets = [Path(a).resolve() for a in args] or [ROOT]
    findings = scan(targets)

    if as_json:
        print(json.dumps({"ok": not findings, "count": len(findings),
                          "findings": findings}, ensure_ascii=False, indent=2))
    elif not findings:
        print(f"PASS  {len(targets)} 个目标：没有发现本体痕迹、路径或凭据")
    else:
        print(f"FAIL  {len(findings)} 处命中，公开前必须清掉：")
        for f in findings:
            print(f"  {f['file']}:{f['line']}  [{f['label']}]  {f['match']}")
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
