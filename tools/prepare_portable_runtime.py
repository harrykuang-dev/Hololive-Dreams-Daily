"""Vendor locked installed packages into an isolated CPython embeddable runtime.

This does not copy venv launchers, settings, caches or developer bytecode.
The embeddable archive must be downloaded separately from the pinned URL.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
from importlib import metadata
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def canonical(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def package_files(environment, packages):
    """Validate all inputs before creating the output runtime."""
    site = (environment/'Lib/site-packages').resolve()
    installed = {}
    for dist in metadata.distributions(path=[str(site)]):
        name = canonical(dist.metadata['Name'])
        if name in installed:
            raise ValueError(f'Duplicate installed distribution: {name}')
        installed[name] = dist
    files = {}
    for name, version in packages.items():
        dist = installed.get(canonical(name))
        if dist is None or dist.version != version:
            raise ValueError(f'Locked distribution is missing or changed: {name}=={version}')
        if not dist.files:
            raise ValueError(f'Distribution has no file inventory: {name}')
        for entry in dist.files:
            relative = PurePosixPath(str(entry).replace('\\', '/'))
            source = Path(dist.locate_file(entry)).resolve()
            if not source.is_relative_to(site):
                if source.is_relative_to((environment/'Scripts').resolve()):
                    continue  # Installed command launchers are not runtime imports.
                if (source.is_relative_to((environment/'share/man').resolve())
                        and source.suffix == '.1'):
                    continue  # Command-line manual pages, not imported assets.
                raise ValueError(f'Distribution file escapes its site directory: {name}/{entry}')
            if ('__pycache__' in relative.parts or source.suffix == '.pyc'
                    or source.name in {'RECORD', 'direct_url.json', 'INSTALLER', 'REQUESTED'}):
                continue
            if not source.is_file():
                raise FileNotFoundError(f'Missing distribution file: {name}/{entry}')
            payload = source.read_bytes()
            if entry.size is not None and len(payload) != entry.size:
                raise ValueError(f'Changed distribution file size: {name}/{entry}')
            if entry.hash is not None:
                actual = base64.urlsafe_b64encode(hashlib.new(entry.hash.mode, payload).digest()).rstrip(b'=').decode()
                if actual != entry.hash.value:
                    raise ValueError(f'Changed distribution file hash: {name}/{entry}')
            target = source.relative_to(site)
            sha = hashlib.sha256(payload).hexdigest()
            if target in files and files[target][1] != sha:
                raise ValueError(f'Conflicting distribution file: {target}')
            files[target] = (source, sha)
    return files


def prepare(archive, environment, group, output):
    lock = json.loads((ROOT/'runtime-packages.lock.json').read_text(encoding='utf-8'))
    if digest(archive) != lock['python']['sha256']:
        raise ValueError('Embeddable Python archive hash does not match the lock')
    if output.exists():
        raise FileExistsError('Runtime output must be a new directory')
    files = package_files(environment.resolve(), lock[group])
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            path = PurePosixPath(entry.filename.replace('\\', '/'))
            if path.is_absolute() or '..' in path.parts or ':' in entry.filename:
                raise ValueError('Unsafe embedded runtime archive path')
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError('Embedded runtime archive must not contain symbolic links')
        bundle.extractall(output)
    site = output/'Lib/site-packages'
    for relative, (source, sha) in files.items():
        destination = site/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if digest(destination) != sha:
            raise OSError(f'Runtime copy verification failed: {relative}')
    (output/'python313._pth').write_text(
        'python313.zip\n.\nLib/site-packages\nLib/site-packages/win32\n'
        'Lib/site-packages/win32/lib\nLib/site-packages/pythonwin\n'
        'Lib/site-packages/pywin32_system32\n', encoding='utf-8')
    if group == 'modules':
        # Python's application directory participates in native DLL loading.
        # Keep pywin32 usable without executing site or user .pth files.
        for name in ('pywintypes313.dll', 'pythoncom313.dll'):
            shutil.copyfile(site/'pywin32_system32'/name, output/name)
    manifest = {'schema_version': 1, 'group': group, 'python': lock['python']['version'],
                'packages': lock[group], 'files': {
                    str(path.relative_to(output)).replace('\\', '/'): digest(path)
                    for path in sorted(output.rglob('*')) if path.is_file()}}
    (output/'runtime-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    return verify(output, group)


def verify(output, group=None):
    manifest = json.loads((output/'runtime-manifest.json').read_text(encoding='utf-8'))
    group = group or manifest['group']
    lock = json.loads((ROOT/'runtime-packages.lock.json').read_text(encoding='utf-8'))
    if (manifest['group'] != group or manifest.get('packages') != lock[group]
            or manifest.get('python') != lock['python']['version']):
        raise ValueError('Runtime package identities do not match the lock')
    expected = set(manifest['files']) | {'runtime-manifest.json'}
    actual = {str(p.relative_to(output)).replace('\\', '/') for p in output.rglob('*') if p.is_file()}
    if actual != expected:
        raise ValueError('Runtime file inventory has changed')
    for relative, sha in manifest['files'].items():
        path = (output/relative).resolve()
        if not path.is_relative_to(output.resolve()) or digest(path) != sha:
            raise ValueError(f'Runtime file verification failed: {relative}')
    imports = ('import maa,cv2,numpy,PIL' if group == 'agent' else
               'import cv2,numpy,PIL,win32gui,win32process,dxcam,onnxruntime,scipy,moderngl')
    code = ("import sys,json,pathlib,site,importlib.util;"+imports+";"
            "root=pathlib.Path(sys.executable).resolve().parent;"
            "assert sys.flags.isolated and not site.ENABLE_USER_SITE;"
            "assert all(pathlib.Path(p).resolve().is_relative_to(root) for p in sys.path);"
            "assert all(pathlib.Path(m.__file__).resolve().is_relative_to(root) "
            "for m in list(sys.modules.values()) if getattr(m,'__file__',None));"
            +("assert importlib.util.find_spec('maa') is None;" if group == 'modules' else '')+
            "print(json.dumps({'isolated':bool(sys.flags.isolated),'version':sys.version.split()[0],"
            "'imports_verified':True,'game_inputs':0}))")
    result = subprocess.run([str(output/'python.exe'), '-I', '-B', '-c', code],
                            capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise RuntimeError('Portable runtime import check failed: '+result.stderr.strip())
    return json.loads(result.stdout.strip().splitlines()[-1])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--environment', type=Path)
    parser.add_argument('--group', choices=('agent', 'modules'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    if args.verify:
        result = verify(args.output.resolve())
    else:
        if not all((args.archive, args.environment, args.group)):
            parser.error('--archive, --environment and --group are required when preparing')
        result = prepare(args.archive, args.environment, args.group, args.output.resolve())
    print(json.dumps(result, indent=2))
