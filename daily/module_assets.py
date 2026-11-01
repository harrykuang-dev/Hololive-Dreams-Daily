"""Verify external model assets against their pinned upstream manifest."""
import hashlib
import json
from pathlib import Path
import re
import shutil


def model_files(module: Path, source: Path | None = None):
    module = Path(module).resolve()
    source = module if source is None else Path(source).resolve()
    manifest = json.loads((module / 'models-manifest.json').read_text(encoding='utf-8'))
    files = manifest.get('files')
    if not isinstance(files, dict) or not files:
        raise ValueError('Model manifest has no files')
    checked = []
    for relative, expected in files.items():
        name = Path(relative)
        if name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0] != 'models':
            raise ValueError('Model manifest path must stay under models/')
        target = (source / name).resolve()
        if not target.is_relative_to(source):
            raise ValueError('Model source path escapes its directory')
        size, digest = expected.get('bytes'), expected.get('sha256')
        if type(size) is not int or size <= 0 or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('Invalid model manifest identity')
        if not target.is_file() or target.stat().st_size != size:
            raise ValueError('Model size mismatch: '+relative)
        with target.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != digest:
            raise ValueError('Model SHA-256 mismatch: '+relative)
        checked.append({'path': relative, 'bytes': size, 'sha256': digest})
    return checked


def prepare_models(module: Path, source: Path):
    module, source = Path(module).resolve(), Path(source).resolve()
    checked = model_files(module, source)
    destinations = [(module / entry['path']).resolve() for entry in checked]
    if any(not path.is_relative_to(module) for path in destinations):
        raise ValueError('Model destination escapes its module directory')
    # Verify every input before copying any model, then verify the destination.
    for entry, destination in zip(checked, destinations):
        original = source / entry['path']
        destination.parent.mkdir(parents=True, exist_ok=True)
        if original.resolve() != destination:
            shutil.copy2(original, destination)
    return model_files(module)
