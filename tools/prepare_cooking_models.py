"""Populate ignored cooking assets from an existing, hash-verified model cache."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from daily.module_assets import model_files, prepare_models


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--module', type=Path, default=ROOT / 'upstream/holodori/upstream/cooking')
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    files = prepare_models(args.module, args.source) if args.source else model_files(args.module)
    print(json.dumps({'verified_files': len(files), 'bytes': sum(row['bytes'] for row in files),
                      'files': files}, indent=2))
