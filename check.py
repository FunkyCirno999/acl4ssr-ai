#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
#  check.py —— 每日心跳 + 上游规则集可达性巡检
# =============================================================================
#
#  产物：STATUS.md
#
#  ── 为什么需要这个脚本 ───────────────────────────────────────────────────────
#
#  GitHub 会在仓库【连续 60 天没有任何提交】后自动禁用定时工作流，而且不发通知。
#
#  本仓库的产物（ACL4SSR_Online_Full_ClaudeAI_MultiMode.ini）里全是 `ruleset=`
#  远程引用，规则内容在【订阅转换的那一刻】才被 subconverter 拉取 ——
#  所以上游即使改了规则，这个 ini 也常常一个字节都不变。
#  结果是：Actions 每天跑，每天都"产物无变化，跳过提交"，60 天后定时任务被禁用，
#  而你以为它还在每天更新。
#
#  解决办法是让每次运行都产生一次提交。但为了"制造提交"而制造提交是噪音，
#  所以顺手做一件真正有用的事：把所有被引用的远程规则集 HEAD 一遍。
#
#  ── 顺带解决的问题：上游规则集 404 ───────────────────────────────────────────
#
#  ini 里引用的规则集全部托管在别人仓库里。任何一个改名/删库，
#  subconverter 转换时就会少一批规则 —— 但你可能几个月都不会发现。
#
#  实测有过这种情况：ACL4SSR 的 Bilibili.list 一度探测 404（后来发现是我探测
#  的 URL 少了 Ruleset/ 子目录）。这类问题用一次 HEAD 就能提前抓到。
#
#  ── 退出码 ───────────────────────────────────────────────────────────────────
#      0 全部可达        1 有规则集不可达（但 STATUS.md 仍写出）
# =============================================================================
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

__version__ = "1.0.0"

DEFAULT_INI = "ACL4SSR_Online_Full_ClaudeAI_MultiMode.ini"
DEFAULT_STATUS = "STATUS.md"
TIMEOUT = 20.0
WORKERS = 8

RULESET_RE = re.compile(r"^\s*;?\s*ruleset\s*=\s*([^,]+),\s*(\S+)\s*$")


def extract_rulesets(ini_text: str):
    """返回 [(group, url)]，跳过注释行与被注释掉的 ruleset。"""
    found = []
    for line in ini_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # 行首带分号 = 被注释掉 —— 不算引用
        if stripped.startswith(";"):
            continue
        m = RULESET_RE.match(line)
        if not m:
            continue
        group, url = m.group(1).strip(), m.group(2).strip()
        if not url.startswith("http"):
            continue
        found.append((group, url))
    return found


def head(url: str):
    """HEAD 请求；有些 CDN 不支持 HEAD 就退回 GET 只读第一个字节。"""
    request = urllib.request.Request(
        url, method="HEAD",
        headers={"User-Agent": "acl4ssr-ai-check/%s" % __version__},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, int(response.headers.get("Content-Length") or 0)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 405):        # 不支持 HEAD
            get = urllib.request.Request(
                url, headers={"User-Agent": "acl4ssr-ai-check/%s" % __version__})
            try:
                with urllib.request.urlopen(get, timeout=TIMEOUT) as response:
                    response.read(1)
                    return response.status, int(response.headers.get("Content-Length") or 0)
            except Exception as inner:    # noqa: BLE001
                return 0, 0
        return exc.code, 0
    except Exception:                     # noqa: BLE001
        return 0, 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="上游规则集可达性巡检 + 每日心跳")
    parser.add_argument("--ini", default=DEFAULT_INI)
    parser.add_argument("--status", default=DEFAULT_STATUS)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    ini_path = Path(args.ini)
    if not ini_path.is_file():
        sys.stderr.write("[check] ERROR: 找不到 %s，先跑 build.py\n" % args.ini)
        return 2

    ini_text = ini_path.read_text(encoding="utf-8")
    rulesets = extract_rulesets(ini_text)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        codes = list(pool.map(lambda pair: head(pair[1]), rulesets))

    bad = []
    rows = []
    for (group, url), (code, size) in zip(rulesets, codes):
        ok = 200 <= code < 300
        if not ok:
            bad.append((group, url, code))
        rows.append((group, url, code, size, ok))

    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# STATUS",
        "",
        "> 本文件由 `check.py` 每个运行日自动写入 —— 请勿手改。",
        ">",
        "> 它的存在有两个作用：",
        "> 1. **让定时工作流活下去。** GitHub 会在仓库连续 60 天没有提交后自动禁用",
        ">    定时工作流。本仓库的 ini 全是远程引用，上游改了它常常不变，",
        ">    所以需要一份每日都会变的文件来产生提交。",
        "> 2. **提前发现上游规则集挂掉。** 这里对每条被引用的 URL 做一次可达性检查，",
        ">    任何一个 404 都会让订阅转换时少一批规则。",
        "",
        "| | |",
        "|---|---|",
        "| 最近巡检 | %s |" % now,
        "| 巡检脚本 | `check.py` v%s |" % __version__,
        "| 被引用规则集 | **%d** 条 |" % len(rulesets),
        "| 不可达 | **%d** 条 |" % len(bad),
        "",
    ]

    if bad:
        lines += [
            "## ⚠️ 不可达的规则集",
            "",
            "| 策略组 | URL | HTTP |",
            "|---|---|---|",
        ]
        for group, url, code in bad:
            lines.append("| `%s` | `%s` | %s |" % (group, url, code or "连接失败"))
        lines.append("")
        lines.append("> 上游可能改了文件名或删了库。请更新 `build.py` 里的 patch，")
        lines.append("> 或到上游仓库确认新路径。**在修好之前，订阅转换会少掉这些规则。**")
        lines.append("")
    else:
        lines += ["## ✅ 全部规则集可达", "",
                  "所有被引用的远程规则集都返回 2xx。", ""]

    lines += ["## 全部规则集", "", "| 策略组 | URL | HTTP | 大小 |", "|---|---|---|---|"]
    for group, url, code, size, ok in rows:
        mark = "" if ok else " ⚠️"
        size_text = ("%.1f KB" % (size / 1024.0)) if size else "-"
        short = url.replace("https://raw.githubusercontent.com/", "")
        short = short.replace("https://cdn.jsdelivr.net/gh/", "jsdelivr:")
        lines.append("| `%s` | `%s` | %s%s | %s |" % (group, short, code or "—", mark, size_text))
    lines.append("")

    Path(args.status).write_text("\n".join(lines) + "\n", encoding="utf-8")

    if not args.quiet:
        print("CHECK_OK rulesets=%d unreachable=%d bad=%s"
              % (len(rulesets), len(bad),
                 ", ".join("%s(%s)" % (g, c) for g, _u, c in bad) if bad else "none"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
