"""Create an inert worker bundle for review; never enroll or install it."""
import argparse
import hashlib
import json
from pathlib import Path

MODULES = ('linux_power.py','remote_job_crypto.py','remote_job_envelope.py',
    'remote_job_protocol.py','remote_job_store.py','remote_job_wire.py',
    'remote_resource_wire.py','remote_syntax_worker.py','remote_worker_service.py')

ENTRY = '''import hashlib, importlib.abc, importlib.util, json, pathlib, sys
sys.dont_write_bytecode = True
root = pathlib.Path(__file__).absolute().parent
def read(path, limit):
    for part in (path,*path.parents):
        if part.is_symlink(): raise ValueError('Linked worker bundle')
    with path.open('rb') as stream: data=stream.read(limit+1)
    if len(data)>limit: raise ValueError('Oversized worker material')
    return data
config=json.loads(read(root/'worker-config.json',4096))
raw=read(root/'bundle-manifest.json',8192)
if hashlib.sha256(raw).hexdigest()!=config['worker_digest']: raise ValueError('Worker identity changed')
manifest=json.loads(raw)
expected={'run-worker.py','prometheus_assistant/__init__.py'}|{'prometheus_assistant/'+n for n in MODULE_NAMES}
if not isinstance(manifest,dict) or set(manifest)!=expected: raise ValueError('Invalid worker manifest')
verified={}
for name,digest in manifest.items():
    data=read(root/name,131072)
    if hashlib.sha256(data).hexdigest()!=digest: raise ValueError('Worker files changed')
    verified[name]=data
args=sys.argv[1:]
if (len(args) not in (2,3) or args[0]!='--root' or
    len(args)==3 and args[2] not in ('--query','--resources') or pathlib.Path(args[1]).absolute()!=root):
    raise ValueError('Worker root differs from installed bundle')
class VerifiedSource(importlib.abc.MetaPathFinder,importlib.abc.Loader):
    def find_spec(self,fullname,path=None,target=None):
        if fullname=='prometheus_assistant':
            return importlib.util.spec_from_loader(fullname,self,is_package=True)
        if fullname.startswith('prometheus_assistant.'):
            if fullname.replace('.','/')+'.py' not in verified: raise ImportError('Unlisted worker module')
            return importlib.util.spec_from_loader(fullname,self)
    def create_module(self,spec): return None
    def exec_module(self,module):
        name=module.__name__.replace('.','/')+('/__init__.py' if module.__name__=='prometheus_assistant' else '.py')
        module.__file__=str(root/name)
        exec(compile(verified[name],module.__file__,'exec'),module.__dict__)
sys.meta_path.insert(0,VerifiedSource())
from prometheus_assistant.remote_worker_service import main
main()
'''


def build(output):
    output=Path(output)
    if not output.is_absolute() or output.exists(): raise ValueError('Choose a new absolute bundle folder.')
    for parent in output.parents:
        if parent.is_symlink(): raise ValueError('Linked bundle destination')
    source=Path(__file__).resolve().parents[1]/'src/prometheus_assistant'
    contents={'prometheus_assistant/'+name:(source/name).read_bytes() for name in MODULES}
    contents['prometheus_assistant/__init__.py']=b''
    contents['run-worker.py']=ENTRY.replace('MODULE_NAMES',repr(MODULES)).encode()
    manifest={name:hashlib.sha256(data).hexdigest() for name,data in sorted(contents.items())}
    raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
    output.mkdir(parents=True)
    for name,data in contents.items():
        path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    (output/'bundle-manifest.json').write_bytes(raw)
    return {'directory':str(output),'worker_digest':hashlib.sha256(raw).hexdigest(),'files':len(manifest),
            'installed':False,'enabled':False,'credentials_created':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    print(json.dumps(build(parser.parse_args().output)))
