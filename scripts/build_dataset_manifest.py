"""Build chronological event groups, without manufacturing outcome labels."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from polymarket_agent.dataset import build_manifest
from polymarket_agent.validation import _time


def main() -> int:
    parser = argparse.ArgumentParser(description='事件分組與時間切分清單；未驗證標籤不訓練')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--train-end', required=True, help='含時區的 ISO 時間，不含此時點')
    parser.add_argument('--validation-end', required=True, help='含時區的 ISO 時間，不含此時點')
    parser.add_argument('--embargo-seconds', type=int, default=86400)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    targets = [args.output_dir / name for name in ['summary.json', 'manifest.jsonl', 'report.md']]
    if any(p.exists() for p in targets):
        parser.error('報告已存在，請使用新的 output-dir')
    summary, records = build_manifest(args.database, _time(args.train_end),
                                      _time(args.validation_end), args.embargo_seconds)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    targets[0].write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    with targets[1].open('w') as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    targets[2].write_text('# 事件分組與標籤準備度\n\n'
                         f"觀測：{summary['observations']}；事件群組：{len(summary['groups'])}\n\n"
                         f"切分：{summary['split_counts']}\n\n"
                         '可訓練：否；已驗證標籤：0\n\n'
                         + '\n'.join('- ' + x for x in summary['limitations']) + '\n')
    print(f'清單完成：{targets[2]}（未通過模型訓練準備度）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
