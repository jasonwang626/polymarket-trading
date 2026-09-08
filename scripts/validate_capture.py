"""Generate offline JSON/Markdown quality reports without modifying source data."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from polymarket_agent.validation import audit_database, markdown_report


def main() -> int:
    parser = argparse.ArgumentParser(description='唯讀檢查已停止掃描的 SQLite 資料庫')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    outputs = [args.output_dir / 'validation.json', args.output_dir / 'validation.md']
    if any(p.exists() for p in outputs):
        parser.error('報告已存在，請選擇新的 output-dir，避免覆寫既有驗證結果')
    report = audit_database(args.database)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs[0].write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    outputs[1].write_text(markdown_report(report))
    print(f'驗證報告：{outputs[1]}')
    return 0 if report['integrity'] == ['ok'] and not report['foreign_key_errors'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
