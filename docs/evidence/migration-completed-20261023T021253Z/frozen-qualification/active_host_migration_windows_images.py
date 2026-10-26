"""Held read-only physical executable pins for original and restarted pairs."""
import ctypes,hashlib,os
from active_host_migration_windows_io import FileInfo
from active_host_migration_windows_processes import q

class ImagePins:
    def __init__(self):
        self.handles={};self.unresolved=[]
        self.open=q.api(q.K,'CreateFileW',[q.W.LPCWSTR,q.W.DWORD,q.W.DWORD,q.W.LPVOID,q.W.DWORD,q.W.DWORD,q.W.HANDLE],q.W.HANDLE)
        self.final=q.api(q.K,'GetFinalPathNameByHandleW',[q.W.HANDLE,q.W.LPWSTR,q.W.DWORD,q.W.DWORD],q.W.DWORD)
        self.info=q.api(q.K,'GetFileInformationByHandle',[q.W.HANDLE,ctypes.POINTER(FileInfo)],q.W.BOOL)
        self.read=q.api(q.K,'ReadFile',[q.W.HANDLE,q.W.LPVOID,q.W.DWORD,ctypes.POINTER(q.W.DWORD),q.W.LPVOID],q.W.BOOL)

    def probe(self,path):
        if self.unresolved:raise RuntimeError('Image pin cleanup uncertainty')
        key=os.path.normcase(os.path.abspath(path))
        if key in self.handles:return self.handles[key][1]
        handle=self.open(path,0x80000000,1,None,3,0x80,None)
        if handle in (None,ctypes.c_void_p(-1).value):raise ctypes.WinError(ctypes.get_last_error())
        try:
            text=ctypes.create_unicode_buffer(32768);length=self.final(handle,text,len(text),0)
            if not length or length>=len(text) or os.path.normcase(text.value)!=os.path.normcase('\\\\?\\'+os.path.abspath(path)):raise ValueError('Executable final path mismatch')
            info=FileInfo();q.require(self.info(handle,ctypes.byref(info)))
            size=(info.size_high<<32)|info.size_low
            if info.attributes&0x400 or not 0<size<=16777216:raise ValueError('Executable reparse/size')
            buffer=ctypes.create_string_buffer(size+1);count=q.W.DWORD();q.require(self.read(handle,buffer,len(buffer),ctypes.byref(count),None))
            if count.value!=size:raise ValueError('Executable changed while pinning')
            value=dict(sha256=hashlib.sha256(buffer.raw[:size]).hexdigest(),file_identity=dict(volume_serial=info.volume,file_index=(info.index_high<<32)|info.index_low))
            self.handles[key]=(handle,value)
            return value
        except BaseException:
            try:
                if not q.Close(handle):raise RuntimeError('CloseHandle failed after pin refusal')
            except BaseException as error:self.unresolved.append(dict(handle=handle,path=path,error=repr(error)))
            raise

    def close(self):
        errors=[]
        for key,(handle,_) in list(self.handles.items()):
            try:
                if not q.Close(handle):raise RuntimeError('CloseHandle failed')
            except BaseException as error:errors.append(dict(handle=handle,path=key,error=repr(error)))
            finally:del self.handles[key]
        self.unresolved.extend(errors)
        if errors:raise RuntimeError('Image handle close uncertainty')
