"""Physical Windows file IO for the production migration store. No CLI.

Every opened file is checked through its handle for final path, owner and DACL.
This supports cooperative exclusion, not protection from an arbitrary same-user
writer racing between checks and rename. Callers own maintenance/runtime locks.
"""
from contextlib import contextmanager
import ctypes,os,ntpath
from ctypes import wintypes as W
from pathlib import Path

class PhysicalRefusal(ValueError):pass

class FileInfo(ctypes.Structure):
    _fields_=[('attributes',W.DWORD),('created',W.FILETIME),('accessed',W.FILETIME),('written',W.FILETIME),('volume',W.DWORD),('size_high',W.DWORD),('size_low',W.DWORD),('links',W.DWORD),('index_high',W.DWORD),('index_low',W.DWORD)]


class PhysicalFiles:
    def __init__(self,root,sid):
        if os.name!='nt':raise PhysicalRefusal('Windows only')
        self.root=Path(root).absolute();self.sid=sid
        self.k=ctypes.WinDLL('kernel32',use_last_error=True);self.a=ctypes.WinDLL('advapi32',use_last_error=True)
        def fn(lib,name,args,result):
            value=getattr(lib,name);value.argtypes=args;value.restype=result;return value
        self.open=fn(self.k,'CreateFileW',[W.LPCWSTR,W.DWORD,W.DWORD,W.LPVOID,W.DWORD,W.DWORD,W.HANDLE],W.HANDLE)
        self.close=fn(self.k,'CloseHandle',[W.HANDLE],W.BOOL)
        self.final=fn(self.k,'GetFinalPathNameByHandleW',[W.HANDLE,W.LPWSTR,W.DWORD,W.DWORD],W.DWORD)
        self.info=fn(self.k,'GetFileInformationByHandle',[W.HANDLE,ctypes.POINTER(FileInfo)],W.BOOL)
        self.read_api=fn(self.k,'ReadFile',[W.HANDLE,W.LPVOID,W.DWORD,ctypes.POINTER(W.DWORD),W.LPVOID],W.BOOL)
        self.write_api=fn(self.k,'WriteFile',[W.HANDLE,W.LPCVOID,W.DWORD,ctypes.POINTER(W.DWORD),W.LPVOID],W.BOOL)
        self.flush=fn(self.k,'FlushFileBuffers',[W.HANDLE],W.BOOL)
        self.security=fn(self.a,'GetSecurityInfo',[W.HANDLE,ctypes.c_int,W.DWORD,ctypes.POINTER(W.LPVOID),ctypes.POINTER(W.LPVOID),ctypes.POINTER(W.LPVOID),ctypes.POINTER(W.LPVOID),ctypes.POINTER(W.LPVOID)],W.DWORD)
        self.sid_text=fn(self.a,'ConvertSidToStringSidW',[W.LPVOID,ctypes.POINTER(W.LPWSTR)],W.BOOL)
        self.ace=fn(self.a,'GetAce',[W.LPVOID,W.DWORD,ctypes.POINTER(W.LPVOID)],W.BOOL)
        self.free=fn(self.k,'LocalFree',[W.LPVOID],W.LPVOID)
        self.path(self.root)
        with self.handle(self.root,directory=True):pass

    def path(self,path):
        raw=os.fspath(path)
        if '..' in raw.replace('/','\\').split('\\') or ':' in ntpath.splitdrive(raw)[1]:raise PhysicalRefusal('Traversal/alternate stream refused')
        value=Path(os.path.abspath(raw))
        try:value.relative_to(self.root)
        except ValueError:raise PhysicalRefusal('Outside fixed owner root')
        for item in (value,*value.parents):
            if item.exists() or item.is_symlink():
                if item.is_symlink() or getattr(item.lstat(),'st_file_attributes',0)&0x400:raise PhysicalRefusal('Reparse ancestor refused')
        return value

    def _sid(self,address):
        text=W.LPWSTR()
        if not self.sid_text(address,ctypes.byref(text)):raise ctypes.WinError(ctypes.get_last_error())
        try:return text.value
        finally:self.free(ctypes.cast(text,W.LPVOID))

    def _acl(self,handle):
        owner=W.LPVOID();dacl=W.LPVOID();descriptor=W.LPVOID()
        status=self.security(handle,1,5,ctypes.byref(owner),None,ctypes.byref(dacl),None,ctypes.byref(descriptor))
        if status:raise ctypes.WinError(status)
        try:
            if not owner or self._sid(owner)!=self.sid or not dacl:raise PhysicalRefusal('Owner or null DACL refused')
            # ACL header: revision/reserved, size, ACE count, reserved.
            header=ctypes.string_at(dacl,8);count=int.from_bytes(header[4:6],'little')
            if count>256:raise PhysicalRefusal('ACL bound')
            # OWNER RIGHTS applies to the owner, already required to equal sid.
            writers={self.sid,'S-1-3-4','S-1-5-18','S-1-5-32-544'}
            write_mask=0x10000000|0x40000000|0x00010000|0x00040000|0x00080000|2|4|16|64|256
            for index in range(count):
                address=W.LPVOID()
                if not self.ace(dacl,index,ctypes.byref(address)):raise ctypes.WinError(ctypes.get_last_error())
                head=ctypes.string_at(address,8);kind,flags=head[0],head[1];size=int.from_bytes(head[2:4],'little')
                if size<8:raise PhysicalRefusal('Malformed ACE')
                if flags&8:continue # Inherit-only does not grant this object access.
                if kind==1:continue # Denial cannot widen write permission.
                if kind!=0:raise PhysicalRefusal('Unsupported effective ACE type')
                mask=int.from_bytes(head[4:8],'little')
                if mask&write_mask and self._sid(address.value+8) not in writers:raise PhysicalRefusal('Unapproved principal can modify migration files')
        finally:
            if descriptor:self.free(descriptor)

    def _verify(self,handle,path):
        text=ctypes.create_unicode_buffer(32768);length=self.final(handle,text,len(text),0)
        if not length or length>=len(text) or os.path.normcase(text.value)!=os.path.normcase('\\\\?\\'+str(path)):raise PhysicalRefusal('Physical handle resolves elsewhere')
        info=FileInfo()
        if not self.info(handle,ctypes.byref(info)):raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes&0x400:raise PhysicalRefusal('Reparse handle')
        self._acl(handle)
        return dict(volume_serial=info.volume,file_index=(info.index_high<<32)|info.index_low,size=(info.size_high<<32)|info.size_low)

    @contextmanager
    def handle(self,path,*,directory=False,create=False):
        path=self.path(path);rights=0x80000000|0x20000|(0x40000000 if create else 0)
        handle=self.open(str(path),rights,0 if create else 1,None,1 if create else 3,0x02000000 if directory else 0x80,None)
        if handle in (None,ctypes.c_void_p(-1).value):raise ctypes.WinError(ctypes.get_last_error())
        try:
            identity=self._verify(handle,path)
            yield handle,identity
        finally:
            if not self.close(handle):raise PhysicalRefusal('Physical file handle close failed')

    def read(self,path,limit=1048576):
        with self.handle(path) as (handle,identity):
            if identity['size']>limit:raise PhysicalRefusal('File read bound')
            buffer=ctypes.create_string_buffer(identity['size']+1);count=W.DWORD()
            if not self.read_api(handle,buffer,len(buffer),ctypes.byref(count),None):raise ctypes.WinError(ctypes.get_last_error())
            if count.value!=identity['size']:raise PhysicalRefusal('File size changed')
            return bytes(buffer.raw[:count.value])

    def create(self,path,data):
        self.check_directory(Path(path).parent)
        with self.handle(path,create=True) as (handle,_):
            buffer=ctypes.create_string_buffer(data);count=W.DWORD()
            if not self.write_api(handle,buffer,len(data),ctypes.byref(count),None) or count.value!=len(data):raise PhysicalRefusal('Incomplete file write preserved')
            if not self.flush(handle):raise ctypes.WinError(ctypes.get_last_error())
        if self.read(path,max(1048576,len(data)))!=data:raise PhysicalRefusal('Create readback mismatch')

    def check_directory(self,path):
        with self.handle(path,directory=True):pass

    def mkdir(self,path):
        path=self.path(path);self.check_directory(path.parent)
        path.mkdir(exist_ok=False);self.check_directory(path)

    def replace(self,source,target,expected):
        source=self.path(source);target=self.path(target)
        self.check_directory(source.parent);self.check_directory(target.parent)
        # Locks exclude cooperative writers. This is deliberately not a hash CAS.
        if self.read(target)!=expected:raise PhysicalRefusal('Target changed before rename')
        with self.handle(source):pass
        os.replace(source,target)

    def publish_new(self,source,target,expected):
        source=self.path(source);target=self.path(target)
        self.check_directory(source.parent);self.check_directory(target.parent)
        if target.exists() or self.read(source)!=expected:raise PhysicalRefusal('Create-only publication conflict')
        # On Windows rename refuses an existing destination; no replace flag.
        os.rename(source,target)
