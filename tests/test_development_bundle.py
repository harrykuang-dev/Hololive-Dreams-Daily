import json
import zipfile

import pytest

from tools import build_development_bundle as bundle


def test_copy_does_not_export_a_file_outside_its_source(tmp_path):
    boundary=tmp_path/'source';boundary.mkdir()
    private=tmp_path/'private.json';private.write_text('not package data')
    destination=tmp_path/'output/private.json'
    with pytest.raises(ValueError,match='escapes'):
        bundle.copy_file(private,destination,boundary)
    assert not destination.exists()


def test_upstream_export_omits_settings_diagnostics_tests_and_recordings(tmp_path,monkeypatch):
    names=['auto_bot.py','LICENSE','README.md','settings.json','daily_coins.json',
           'templates/card.png','models/rope.onnx','outputs/kitchen-bot/standalone.py',
           'outputs/kitchen-assets/textures/milk.png',
           'outputs/private.json','diagnostics/frame.png','assets/recording.mp4','tests/account.json']
    monkeypatch.setattr(bundle,'git',lambda *a:'\0'.join(names))
    assert {p.as_posix() for p in bundle.module_files(tmp_path)}=={
        'auto_bot.py','LICENSE','README.md','templates/card.png','models/rope.onnx',
        'outputs/kitchen-bot/standalone.py','outputs/kitchen-assets/textures/milk.png'}


def test_existing_bundle_is_not_overwritten(tmp_path):
    with pytest.raises(FileExistsError):
        bundle.build(None,None,None,tmp_path)


def test_built_bundle_places_the_first_run_template_where_the_gui_reads_it(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir()
    monkeypatch.setattr(bundle,'ROOT',source)
    archive=tmp_path/'gui.zip'
    with zipfile.ZipFile(archive,'w') as stream:stream.writestr('MFAAvalonia.exe',b'fixture')
    (source/'dependencies.lock.json').write_text(json.dumps({
        'mfa_archive_sha256':bundle.digest(archive),'holodori':{'commit':'pinned','modules':{}}}))
    (source/'interface.json').write_text(json.dumps({'agent':{'child_exec':'./runtime/python/python.exe'}}))
    template={'CurrentLanguage':'zh-Hant','UI.HasCompletedFirstUseTutorial':True}
    (source/'config.template.json').write_text(json.dumps(template))
    upstream=source/'upstream/holodori';upstream.mkdir(parents=True)
    for name in ('LICENSES.md','THIRD_PARTY_NOTICES.md'):(upstream/name).write_text('fixture notice')
    runtime=tmp_path/'runtime';runtime.mkdir()
    def git(directory,*args):
        if args[0]=='status':return ''
        if args[0]=='rev-parse':return 'pinned'
        if args[0]=='ls-files':return 'interface.json\0config.template.json'
        pytest.fail(str(args))
    monkeypatch.setattr(bundle,'git',git)
    monkeypatch.setattr(bundle,'verify_runtime',lambda *a:None)
    from daily import module_assets
    monkeypatch.setattr(module_assets,'model_files',lambda source:[])
    output=tmp_path/'bundle'
    bundle.build(archive,runtime,runtime,output)
    assert json.loads((output/'config/config.template.json').read_text())==template
    assert not (output/'config.template.json').exists()


def test_wrong_gui_archive_stops_before_output_or_runtime_loading(tmp_path,monkeypatch):
    monkeypatch.setattr(bundle,'ROOT',tmp_path)
    monkeypatch.setattr(bundle,'git',lambda *a:'')
    (tmp_path/'dependencies.lock.json').write_text(json.dumps({'mfa_archive_sha256':'0'*64}))
    archive=tmp_path/'gui.zip';archive.write_bytes(b'changed archive')
    monkeypatch.setattr(bundle,'verify_runtime',lambda *a:pytest.fail('Must not load runtimes'))
    output=tmp_path/'output'
    with pytest.raises(ValueError,match='GUI archive hash'):
        bundle.build(archive,tmp_path,tmp_path,output)
    assert not output.exists()


def test_pristine_bundle_verification_rejects_extra_recordings(tmp_path):
    (tmp_path/'package-manifest.json').write_text(json.dumps({'files':{}}))
    (tmp_path/'recording.avi').write_bytes(b'private recording')
    with pytest.raises(ValueError,match='inventory'):
        bundle.verify(tmp_path)


def test_bundle_detects_modified_files_before_reading_configuration(tmp_path):
    (tmp_path/'interface.json').write_text('{}')
    (tmp_path/'package-manifest.json').write_text(json.dumps({'files':{'interface.json':'0'*64}}))
    with pytest.raises(ValueError,match='hash mismatch'):
        bundle.verify(tmp_path)
