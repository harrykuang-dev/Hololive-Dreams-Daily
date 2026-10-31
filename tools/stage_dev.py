"""Stage a local development GUI without modifying the canonical PI paths."""
import argparse
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def stage(gui: Path, python: Path, output: Path):
    if not (gui / 'MFAAvalonia.exe').is_file() or not python.is_file():
        raise FileNotFoundError('GUI runtime or development interpreter is missing')
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(gui, output, dirs_exist_ok=True)
    for directory in ('daily', 'agent', 'resource'):
        shutil.copytree(ROOT / directory, output / directory, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__'))
    for name in ('interface_zh_tw.json', 'interface_en_us.json', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT / name, output / name)
    interface = json.loads((ROOT / 'interface.json').read_text(encoding='utf-8'))
    interface['agent']['child_exec'] = str(python.resolve())
    (output / 'interface.json').write_text(json.dumps(interface, ensure_ascii=False, indent=2), encoding='utf-8')
    config = output / 'config'
    config.mkdir(exist_ok=True)
    shutil.copy2(ROOT / 'config.template.json', config / 'config.template.json')
    print(output / 'MFAAvalonia.exe')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--gui-runtime', type=Path, required=True)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    stage(args.gui_runtime, args.python, args.output)
