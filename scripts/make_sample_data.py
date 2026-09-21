#!/usr/bin/env python3
"""生成两个展柜的示例照度 JSONL(含人为断点),供上传测试。

用法: python scripts/make_sample_data.py
输出: sample_data/case_a.jsonl, sample_data/case_b.jsonl
"""
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

random.seed(42)
OUT = Path(__file__).resolve().parent.parent / "sample_data"
OUT.mkdir(exist_ok=True)

base = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=120)

# (文件名, 基准照度, 噪声, 断点起始日列表) —— 断点处删除 3 天数据模拟传感器离线
CASES = [
    ("case_a.jsonl", 60, 15, [35, 80]),    # 临窗展厅,照度较高(~50 lux)
    ("case_b.jsonl", 17, 5, [60]),         # 内厅恒温柜,低照度(~15 lux)
]

for fname, level, noise, gap_days in CASES:
    lines = []
    for day in range(120):
        for hour in (8, 20):  # 开馆 / 闭馆各一条
            if any(g <= day < g + 3 for g in gap_days):
                continue  # 断点:传感器离线
            ts = base + timedelta(days=day, hours=hour)
            daytime = math.sin(math.pi * (hour - 8) / 12) if 8 <= hour <= 20 else 0
            weekend = 1.25 if ts.weekday() >= 5 else 1.0
            lux = max(0.0, level * (0.35 + 0.65 * daytime) * weekend
                      + random.uniform(-noise, noise))
            lines.append(json.dumps({
                "ts": ts.isoformat().replace("+00:00", "Z"),
                "lux": round(lux, 1),
            }))
    path = OUT / fname
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{path}: {len(lines)} 条读数")
