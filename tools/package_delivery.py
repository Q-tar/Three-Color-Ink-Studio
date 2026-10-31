"""Create source and Windows runtime ZIPs without copying local build caches."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {'.git', '.venv', 'venv', '__pycache__', 'build', 'dist',
             'backups', 'logs', '.vscode', '.pytest_cache'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def source_files():
    for directory, dirs, files in os.walk(ROOT, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS
                         and not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if path.is_symlink() or path.suffix.lower() in {'.pyc', '.pyo', '.bak'}:
                continue
            yield path, 'epd_hello_idf/' + path.relative_to(ROOT).as_posix()


def runtime_files():
    base = ROOT / 'image_tool/dist/InkStudio'
    if not (base / 'InkStudio.exe').is_file():
        raise RuntimeError('Build the EXE with image_tool/package.ps1 first')
    for path in sorted(base.rglob('*')):
        if path.is_file() and not path.is_symlink():
            yield path, 'InkStudio/' + path.relative_to(base).as_posix()
    yield ROOT / '交付说明.md', '交付说明.md'


def make_zip(target, files):
    if target.exists():
        raise FileExistsError(f'Refusing to overwrite: {target}')
    records = {}
    with zipfile.ZipFile(target, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path, name in files:
            data = path.read_bytes()
            archive.writestr(name, data)
            records[name] = {'bytes': len(data), 'sha256': digest(data)}
        archive.writestr('文件校验.json', json.dumps(records, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise RuntimeError(f'ZIP CRC check failed: {target}')
        for name, record in records.items():
            if digest(archive.read(name)) != record['sha256']:
                raise RuntimeError(f'SHA256 mismatch: {name}')
    return {'file': target.name, 'bytes': target.stat().st_size,
            'sha256': digest(target.read_bytes()), 'files': len(records)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    output = options.output.resolve()
    if output == ROOT or ROOT in output.parents:
        raise ValueError('Choose an output directory outside the project')
    output.mkdir(parents=True, exist_ok=True)
    records = [make_zip(output / '墨水屏完整源码.zip', source_files()),
               make_zip(output / '墨水屏上位机使用包.zip', runtime_files())]
    (output / '交付说明.md').write_bytes((ROOT / '交付说明.md').read_bytes())
    (output / '压缩包校验.json').write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(records, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
