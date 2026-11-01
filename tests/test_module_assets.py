import hashlib
import json

import pytest

from daily.module_assets import model_files, prepare_models


def manifest(root, files):
    root.mkdir()
    rows = {}
    for relative, data in files.items():
        rows[relative] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    (root/'models-manifest.json').write_text(json.dumps({'files':rows}), encoding='utf-8')


def test_unverified_cache_never_populates_any_model(tmp_path):
    module, cache = tmp_path/'module', tmp_path/'cache'
    manifest(module, {'models/a.onnx':b'first', 'models/b.onnx':b'second'})
    (cache/'models').mkdir(parents=True)
    (cache/'models/a.onnx').write_bytes(b'first')
    (cache/'models/b.onnx').write_bytes(b'WRONG!')
    with pytest.raises(ValueError, match='SHA-256'):
        prepare_models(module, cache)
    assert not (module/'models').exists()


def test_verified_cache_and_copied_models_match_the_same_pinned_manifest(tmp_path):
    module, cache = tmp_path/'module', tmp_path/'cache'
    manifest(module, {'models/a.onnx':b'first'})
    (cache/'models').mkdir(parents=True)
    (cache/'models/a.onnx').write_bytes(b'first')
    assert prepare_models(module, cache) == model_files(module)
    assert (module/'models/a.onnx').read_bytes() == b'first'


@pytest.mark.parametrize('path', ['../outside.onnx', '/outside.onnx', 'models/../../outside.onnx'])
def test_manifest_cannot_escape_the_model_directory(tmp_path, path):
    module = tmp_path/'module'
    manifest(module, {path:b'first'})
    with pytest.raises(ValueError, match='path'):
        model_files(module)
