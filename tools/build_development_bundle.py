"""Build a local development bundle from pinned archives and clean source.

No publishing or installer execution is performed. .NET10 and the Microsoft
Visual C++ runtime remain prerequisites of the framework-dependent GUI.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.prepare_portable_runtime import digest, verify as verify_runtime
PRIVATE_PARTS = {'__pycache__', '.git', '.github', 'tests', 'userdata', 'diagnostics', 'recordings', 'config'}


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args], text=True, encoding='utf-8').strip()


def copy_file(source, destination, boundary):
    if not source.resolve().is_relative_to(boundary.resolve()) or source.is_symlink():
        raise ValueError('Package input escapes its source directory')
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    if digest(source) != digest(destination):
        raise OSError('Package file copy verification failed')


def module_files(source):
    for name in git(source, 'ls-files', '-z').split('\0'):
        if not name:
            continue
        path = PurePosixPath(name)
        if any(part in PRIVATE_PARTS for part in path.parts):
            continue
        if path.suffix.lower() in {'.avi', '.mp4', '.log', '.pyc'}:
            continue
        if len(path.parts) == 1:
            if not (path.suffix == '.py' or path.name.startswith(('LICENSE','NOTICE','README','THIRD_PARTY','requirements','models-manifest'))):
                continue
        elif path.parts[0] not in {'assets','templates','models','outputs','controller'}:
            continue
        if path.parts[0] == 'outputs' and path.parts[1] not in {'kitchen-bot','kitchen-assets'}:
            continue
        yield path


def build(gui_archive, agent_runtime, module_runtime, output):
    if output.exists():
        raise FileExistsError('Bundle output must be a new directory')
    if git(ROOT, 'status', '--porcelain'):
        raise ValueError('Commit the checked source before building a bundle')
    lock = json.loads((ROOT/'dependencies.lock.json').read_text(encoding='utf-8'))
    if digest(gui_archive) != lock['mfa_archive_sha256']:
        raise ValueError('GUI archive hash does not match dependencies.lock.json')
    verify_runtime(agent_runtime, 'agent')
    verify_runtime(module_runtime, 'modules')
    upstream = ROOT/'upstream/holodori'
    if git(upstream, 'rev-parse', 'HEAD') != lock['holodori']['commit']:
        raise ValueError('Holodori commit does not match its lock')
    for name, record in lock['holodori']['modules'].items():
        source = upstream/record['path']
        if (git(source, 'rev-parse', 'HEAD') != record['commit']
                or git(source, 'diff', 'HEAD', '--name-only')):
            raise ValueError(f'Pinned upstream source has changed: {name}')
    with zipfile.ZipFile(gui_archive) as archive:
        for item in archive.infolist():
            path = PurePosixPath(item.filename.replace('\\','/'))
            if path.is_absolute() or '..' in path.parts or ':' in item.filename or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('Unsafe GUI archive path')
        archive.extractall(output)
    for name in git(ROOT, 'ls-files', '-z').split('\0'):
        path = PurePosixPath(name)
        if (path.parts and path.parts[0] in {'agent','daily','resource'}
                or len(path.parts) == 1 and (name.startswith(('interface','README','THIRD_PARTY'))
                    or name in {'config.template.json','dependencies.lock.json','runtime-packages.lock.json'})):
            destination = output/'config/config.template.json' if name == 'config.template.json' else output/name
            copy_file(ROOT/name, destination, ROOT)
    for source, name in ((agent_runtime,'python'),(module_runtime,'modules')):
        for item in source.rglob('*'):
            if item.is_file():
                copy_file(item, output/'runtime'/name/item.relative_to(source), source)
    for name in ('LICENSES.md','THIRD_PARTY_NOTICES.md'):
        copy_file(upstream/name, output/'upstream/holodori'/name, upstream)
    for name, record in lock['holodori']['modules'].items():
        source = upstream/record['path']
        destination = output/'upstream/holodori'/record['path']
        for path in module_files(source):
            file = source/str(path)
            # Git LFS tracks a pointer; the working model must match its oid.
            if file.suffix.lower() in {'.onnx','.pt'}:
                blob = subprocess.check_output(['git','-C',str(source),'show',f'HEAD:{path.as_posix()}'])
                if blob.startswith(b'version https://git-lfs.github.com/spec/v1'):
                    fields = dict(line.split(' ',1) for line in blob.decode().splitlines()[1:])
                    if fields['oid'] != 'sha256:'+digest(file) or int(fields['size']) != file.stat().st_size:
                        raise ValueError(f'Unmaterialized or changed LFS model: {name}/{path}')
            copy_file(file, destination/str(path), source)
    # Cooking's ignored model assets are independently pinned by its manifest.
    from daily.module_assets import model_files
    cooking = upstream/'upstream/cooking'
    for entry in model_files(cooking):
        path = Path(entry['path'])
        copy_file(cooking/path, output/'upstream/holodori/upstream/cooking'/path, cooking)
    (output/'BUNDLE.md').write_text(
        '# Development bundle / 開發測試包\n\n'
        'Daily execution remains incomplete. This is a local development build, not a binary release.\n'
        '完整日常仍未驗收；此為本機開發測試包。\n\n'
        'Requires Windows x64, .NET10 and the Microsoft Visual C++ runtime. The game must already be open.\n'
        '須具備 Windows x64、.NET10 與 Microsoft Visual C++ 執行階段，並先開啟遊戲。\n'
        'Only Connect is selected by default. Cooking and Live round counts must be entered.\n'
        '預設只勾選連接遊戲；料理及 Live 局數須自行填寫。\n', encoding='utf-8')
    manifest = {'schema_version':1, 'development':True, 'source_commit':git(ROOT,'rev-parse','HEAD'),
                'dependencies':lock, 'files':{str(p.relative_to(output)).replace('\\','/'):digest(p)
                    for p in sorted(output.rglob('*')) if p.is_file()}}
    (output/'package-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return verify(output)


def verify(output):
    record = json.loads((output/'package-manifest.json').read_text(encoding='utf-8'))
    paths = {str(p.relative_to(output)).replace('\\','/') for p in output.rglob('*') if p.is_file()}
    if paths != set(record['files'])|{'package-manifest.json'}:
        raise ValueError('Bundle file inventory has changed')
    for name, expected in record['files'].items():
        path = (output/name).resolve()
        if not path.is_relative_to(output.resolve()) or digest(path) != expected:
            raise ValueError(f'Bundle hash mismatch: {name}')
    interface = json.loads((output/'interface.json').read_text(encoding='utf-8'))
    if interface['agent']['child_exec'] != './runtime/python/python.exe':
        raise ValueError('Agent does not use the relative bundled interpreter')
    if (output/'config/modules.json').exists():
        raise ValueError('Bundle must not contain a development module-path override')
    return {'files':len(paths),'bytes':sum(p.stat().st_size for p in output.rglob('*') if p.is_file()),
            'development':True,'source_commit':record['source_commit']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--gui-archive',type=Path)
    parser.add_argument('--agent-runtime',type=Path)
    parser.add_argument('--module-runtime',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--verify',action='store_true')
    args = parser.parse_args()
    if args.verify:
        result = verify(args.output.resolve())
    else:
        if not all((args.gui_archive,args.agent_runtime,args.module_runtime)):
            parser.error('Archive and both runtimes are required')
        result = build(args.gui_archive,args.agent_runtime,args.module_runtime,args.output.resolve())
    print(json.dumps(result,indent=2))
