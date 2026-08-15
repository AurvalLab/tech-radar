#!/usr/bin/env python3
"""Generate durable Tech Radar reports for scheduled cloud runs."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

import tech_radar as radar


LOGGER = logging.getLogger("tech-radar.cloud")
SHANGHAI_TZ = timezone(timedelta(hours=8))
REPORT_DIR = Path(os.getenv("TECH_RADAR_REPORT_DIR", "reports"))


def atomic_write_text(path: Path, text: str) -> None:
    """Write UTF-8 text atomically so readers never see a partial report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def build_payload(items: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    generated_at = radar.utc_now()
    public_items = [radar.public_item(item) for item in items]
    return {
        "report_date": generated_at.astimezone(SHANGHAI_TZ).date().isoformat(),
        "generated_at": generated_at.isoformat(),
        "timezone": "Asia/Shanghai",
        "items": public_items,
    }


def render_markdown(payload: Dict[str, Any]) -> str:
    lines: List[str] = [
        "# 技术雷达日报",
        "",
        f"- 日期：{payload['report_date']}",
        "- 时区：Asia/Shanghai",
        f"- 条目：{len(payload['items'])}",
        "",
    ]

    if not payload["items"]:
        lines.append("今日暂无新的高信号技术动态。")
        return "\n".join(lines) + "\n"

    for index, item in enumerate(payload["items"], start=1):
        score = float(item.get("score") or 0)
        stars = int(item.get("stars") or 0)
        points = int(item.get("points") or 0)
        signal = f"{points} points" if points else f"{stars} stars"
        description = (
            item.get("original_text")
            or item.get("description")
            or "暂无简介"
        )

        lines.extend(
            [
                f"## {index}. {item.get('name') or item.get('title')}",
                "",
                f"- 信号：{score:.1f} 分 · {signal}",
                f"- 链接：{item.get('url')}",
                "",
                str(description).strip(),
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def publish() -> Dict[str, Any]:
    items = radar.get_unseen_batch(radar.MAX_PER_TICK)
    payload = build_payload(items)
    report_date = payload["report_date"]

    latest_json = REPORT_DIR / "latest.json"
    latest_markdown = REPORT_DIR / "latest.md"
    archive_dir = REPORT_DIR / "archive"

    radar.atomic_write_json(latest_json, payload)
    atomic_write_text(latest_markdown, render_markdown(payload))
    radar.atomic_write_json(archive_dir / f"{report_date}.json", payload)
    atomic_write_text(
        archive_dir / f"{report_date}.md",
        render_markdown(payload),
    )

    radar.commit_items(items)
    radar.clear_pending()
    LOGGER.info("Published %d radar items for %s", len(items), report_date)
    return payload


def main() -> int:
    radar.configure_stdio()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    payload = publish()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
