"""Replay quality decisions from captured observations without network access."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from polymarket_agent.config import load_settings
from polymarket_agent.replay import replay_capture


def main() -> int:
    parser = argparse.ArgumentParser(description='唯讀歷史行情品質重播；不建立交易')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--config', type=Path)
    args = parser.parse_args()
    targets = [args.output_dir / name for name in ['summary.json', 'events.jsonl', 'report.md']]
    if any(p.exists() for p in targets):
        parser.error('報告已存在，請選擇新的 output-dir')
    report, events = replay_capture(args.database, load_settings(args.config))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    targets[0].write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    with targets[1].open('w') as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n')
    targets[2].write_text('# 歷史行情品質重播\n\n'
                         f"觀測筆數：{report['observations']}\n\n"
                         f"分類：{report['statuses']}\n\n"
                         f"來源省略紀錄：{report['omitted_input_rows']}\n\n"
                         + '\n'.join('- ' + x for x in report['limitations']) + '\n')
    print(f'重播完成：{targets[2]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
