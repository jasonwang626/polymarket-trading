"""Save bounded public GET evidence without connecting a trading account."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from polymarket_agent.config import assert_no_trading_secrets, load_settings
from polymarket_agent.data.public_http import PublicAPI
from polymarket_agent.settlement import collect_evidence


async def collect(args):
    assert_no_trading_secrets()
    slugs = list(dict.fromkeys(args.slug))
    if not 1 <= len(slugs) <= 20:
        raise ValueError('Collect 1..20 distinct BTC market slugs per run')
    # Reserve a new directory before requesting data; no overwrite or mixing runs.
    args.output_dir.mkdir(parents=True, exist_ok=False)
    api = PublicAPI(load_settings(args.config))
    unavailable = 0
    try:
        for slug in slugs:
            evidence = await collect_evidence(api, slug)
            target = args.output_dir / f'{slug}.json'
            with target.open('x') as handle:
                json.dump(evidence, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write('\n')
            unavailable += any(r['status'] != 'ok' for r in evidence['responses'])
            print(f"{slug}: {evidence['assessment']['reasons']}")
            if any(r.get('http_status') in {401, 403, 451} for r in evidence['responses']):
                break
    finally:
        await api.close()
    return 1 if unavailable else 0


def main():
    parser = argparse.ArgumentParser(description='收集公開結算證據；不自動生成訓練標籤')
    parser.add_argument('--slug', action='append', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--config', type=Path)
    return asyncio.run(collect(parser.parse_args()))


if __name__ == '__main__':
    raise SystemExit(main())
