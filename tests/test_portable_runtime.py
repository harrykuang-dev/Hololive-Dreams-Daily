import base64
import csv
import hashlib
import json

import pytest

from tools import prepare_portable_runtime as runtime


def installed(tmp_path):
    environment = tmp_path/'venv'
    site = environment/'Lib/site-packages'
    meta = site/'demo-1.0.dist-info'
    meta.mkdir(parents=True)
    (meta/'METADATA').write_text('Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n')
    module = site/'demo.py'
    module.write_bytes(b'value = 1\n')
    (site/'private.json').write_text('not part of any distribution')
    (meta/'direct_url.json').write_text('{"url":"file:///private/build/location"}')
    cache = site/'__pycache__'; cache.mkdir(); (cache/'demo.pyc').write_bytes(b'private compiler path')
    with (meta/'RECORD').open('w', newline='') as stream:
        csv.writer(stream).writerows([
            ['demo.py', 'sha256='+base64.urlsafe_b64encode(hashlib.sha256(module.read_bytes()).digest()).rstrip(b'=').decode(), module.stat().st_size],
            ['demo-1.0.dist-info/METADATA','',''], ['demo-1.0.dist-info/direct_url.json','',''],
            ['__pycache__/demo.pyc','',''], ['demo-1.0.dist-info/RECORD','','']])
    return environment, site, meta


def test_only_locked_recorded_files_are_selected_without_private_metadata_or_bytecode(tmp_path):
    environment, _, _ = installed(tmp_path)
    files = runtime.package_files(environment, {'demo':'1.0'})
    assert {str(p).replace('\\','/') for p in files} == {'demo.py','demo-1.0.dist-info/METADATA'}


def test_changed_installed_file_is_rejected_against_its_record_hash(tmp_path):
    environment, site, _ = installed(tmp_path)
    (site/'demo.py').write_bytes(b'value = 2\n')
    with pytest.raises(ValueError, match='file hash'):
        runtime.package_files(environment, {'demo':'1.0'})


def test_record_cannot_copy_files_outside_the_environment_site_directory(tmp_path):
    environment, _, meta = installed(tmp_path)
    (tmp_path/'private.json').write_text('outside distribution')
    with (meta/'RECORD').open('a', newline='') as stream:
        csv.writer(stream).writerow(['../../../private.json','',''])
    with pytest.raises(ValueError, match='escapes'):
        runtime.package_files(environment, {'demo':'1.0'})


def test_an_unlocked_package_version_is_not_used_as_a_substitute(tmp_path):
    environment, _, _ = installed(tmp_path)
    with pytest.raises(ValueError, match='missing or changed'):
        runtime.package_files(environment, {'demo':'2.0'})


def test_bad_python_archive_is_rejected_before_an_output_is_created(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'ROOT', tmp_path)
    (tmp_path/'runtime-packages.lock.json').write_text(json.dumps({'python':{'sha256':'0'*64}}))
    archive = tmp_path/'python.zip'; archive.write_bytes(b'changed archive')
    output = tmp_path/'output'
    with pytest.raises(ValueError, match='archive hash'):
        runtime.prepare(archive, tmp_path/'venv', 'agent', output)
    assert not output.exists()


def test_extra_runtime_settings_fail_inventory_before_python_is_launched(tmp_path, monkeypatch):
    lock=json.loads((runtime.ROOT/'runtime-packages.lock.json').read_text())
    (tmp_path/'runtime-manifest.json').write_text(json.dumps({'group':'agent','files':{},
        'python':lock['python']['version'],'packages':lock['agent']}))
    (tmp_path/'private.json').write_text('unexpected settings')
    monkeypatch.setattr(runtime.subprocess, 'run', lambda *a,**k: pytest.fail('Must not launch'))
    with pytest.raises(ValueError, match='inventory'):
        runtime.verify(tmp_path)
