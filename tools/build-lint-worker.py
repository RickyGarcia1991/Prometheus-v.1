"""Build an inert, pinned Linux lint-worker candidate; never enroll it."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re


def _plain(path):
    for part in (path,*path.parents):
        if part.is_symlink() or getattr(part.stat(),'st_file_attributes',0)&0x400:
            raise ValueError('Linked worker material is not accepted.')


def build(output, executable, version):
    output,executable=Path(output),Path(executable)
    if not output.is_absolute() or output.exists():
        raise ValueError('Choose a new absolute bundle folder.')
    if not executable.is_absolute() or not executable.is_file():
        raise ValueError('Choose an existing absolute Linux Ruff executable.')
    _plain(executable)
    for parent in output.parents:
        if parent.exists(): _plain(parent)
    if not isinstance(version,str) or re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',version) is None:
        raise ValueError('An exact release version is required.')
    if not 4<=executable.stat().st_size<=128*1024*1024:
        raise ValueError('Executable size exceeds the bundle bound.')
    binary=executable.read_bytes()
    if binary[:4]!=b'\x7fELF':
        raise ValueError('A Linux ELF executable is required; it is not run by this builder.')
    tool={'sha256':hashlib.sha256(binary).hexdigest(),'version':version}
    base_path=Path(__file__).with_name('build-syntax-worker.py')
    spec=importlib.util.spec_from_file_location('syntax_bundle_base',base_path)
    base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
    modules=(*base.MODULES,'remote_lint_runner.py','remote_lint_worker.py')
    source=Path(__file__).resolve().parents[1]/'src/prometheus_assistant'
    contents={'prometheus_assistant/'+name:(source/name).read_bytes() for name in modules}
    contents['prometheus_assistant/__init__.py']=b''
    contents['ruff-tool.json']=json.dumps(dict(schema_version=1,profile='ruff-python-v1',
        tool=tool,executable='tools/ruff'),sort_keys=True,separators=(',',':')).encode()
    entry=base.ENTRY.replace('MODULE_NAMES',repr(modules))
    needle="if not isinstance(manifest,dict) or set(manifest)!=expected:"
    assert entry.count(needle)==1
    entry=entry.replace(needle,"expected|={'ruff-tool.json','tools/ruff'}\n"+needle)
    needle='    data=read(root/name,131072)'
    assert entry.count(needle)==1
    entry=entry.replace(needle,"""    if name=='tools/ruff':
        path=root/name
        for part in (path,*path.parents):
            if part.is_symlink(): raise ValueError('Linked lint executable')
        if not path.is_file() or not 4<=path.stat().st_size<=134217728: raise ValueError('Invalid lint executable size')
        checksum=hashlib.sha256()
        with path.open('rb') as stream:
            total=0
            while chunk:=stream.read(1048576):
                total+=len(chunk)
                if total>134217728: raise ValueError('Oversized lint executable')
                checksum.update(chunk)
        if checksum.hexdigest()!=digest: raise ValueError('Lint executable changed')
        continue
    data=read(root/name,131072)""")
    contents['run-worker.py']=entry.encode()
    manifest={name:hashlib.sha256(data).hexdigest() for name,data in contents.items()}
    manifest['tools/ruff']=tool['sha256']
    raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
    output.mkdir(parents=True)
    for name,data in contents.items():
        path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    binary_path=output/'tools/ruff';binary_path.parent.mkdir();binary_path.write_bytes(binary)
    binary_path.chmod(0o700)
    (output/'bundle-manifest.json').write_bytes(raw)
    return dict(directory=str(output),worker_digest=hashlib.sha256(raw).hexdigest(),tool=tool,
        files=len(manifest),installed=False,enabled=False,credentials_created=False,
        native_tool_version_verified=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True);parser.add_argument('--ruff',required=True)
    parser.add_argument('--version',required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.output,args.ruff,args.version)))
