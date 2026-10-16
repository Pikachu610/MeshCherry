"""Own a Windows process tree; closing the Job Object kills descendants too."""
import ctypes
from ctypes import wintypes


class WindowsJob:
    def __init__(self):
        class Basic(ctypes.Structure):
            _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),
                ('flags',wintypes.DWORD),('minimum_working_set',ctypes.c_size_t),
                ('maximum_working_set',ctypes.c_size_t),('active_processes',wintypes.DWORD),
                ('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_=[(name,ctypes.c_uint64) for name in
                ('reads','writes','other','read_bytes','write_bytes','other_bytes')]
        class Extended(ctypes.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),
                ('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        self.api=ctypes.WinDLL('kernel32',use_last_error=True)
        self.api.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype=wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        self.api.SetInformationJobObject.restype=wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype=wintypes.BOOL
        self.api.CloseHandle.argtypes=[wintypes.HANDLE]; self.api.CloseHandle.restype=wintypes.BOOL
        self.handle=self.api.CreateJobObjectW(None,None)
        if not self.handle: raise ctypes.WinError(ctypes.get_last_error())
        info=Extended();info.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle,9,ctypes.byref(info),ctypes.sizeof(info)):
            error=ctypes.WinError(ctypes.get_last_error());self.close();raise error

    def assign(self,process):
        # Child is created by our Popen; no user-supplied PID is accepted.
        if not self.api.AssignProcessToJobObject(self.handle,wintypes.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())

    def resume(self,process):
        """Resume only our CREATE_SUSPENDED process after successful assignment.

        Popen closes the original primary-thread handle. Use documented Tool Help
        to acquire that owned process's initial thread, not a global Popen patch.
        Before resume the application cannot execute or create descendants.
        """
        class ThreadEntry(ctypes.Structure):
            _fields_=[('size',wintypes.DWORD),('usage',wintypes.DWORD),
                ('thread_id',wintypes.DWORD),('owner_pid',wintypes.DWORD),
                ('priority',wintypes.LONG),('delta',wintypes.LONG),('flags',wintypes.DWORD)]
        api=self.api
        api.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]
        api.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
        api.Thread32First.argtypes=[wintypes.HANDLE,ctypes.POINTER(ThreadEntry)]
        api.Thread32First.restype=wintypes.BOOL
        api.Thread32Next.argtypes=[wintypes.HANDLE,ctypes.POINTER(ThreadEntry)]
        api.Thread32Next.restype=wintypes.BOOL
        api.OpenThread.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        api.OpenThread.restype=wintypes.HANDLE
        api.ResumeThread.argtypes=[wintypes.HANDLE];api.ResumeThread.restype=wintypes.DWORD
        snapshot=api.CreateToolhelp32Snapshot(0x4,0)  # TH32CS_SNAPTHREAD
        if snapshot==ctypes.c_void_p(-1).value: raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry=ThreadEntry();entry.size=ctypes.sizeof(entry)
            found=api.Thread32First(snapshot,ctypes.byref(entry))
            while found:
                if entry.owner_pid==process.pid:
                    thread=api.OpenThread(0x2,False,entry.thread_id)  # THREAD_SUSPEND_RESUME
                    if not thread: raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        previous=api.ResumeThread(thread)
                        if previous==0xffffffff: raise ctypes.WinError(ctypes.get_last_error())
                        if previous!=1: raise RuntimeError('Owned initial thread was not suspended exactly once')
                    finally: api.CloseHandle(thread)
                    return
                entry.size=ctypes.sizeof(entry)
                found=api.Thread32Next(snapshot,ctypes.byref(entry))
            raise RuntimeError('Cannot find suspended initial thread for owned process')
        finally: api.CloseHandle(snapshot)

    def close(self):
        if self.handle:
            handle=self.handle;self.handle=None
            if not self.api.CloseHandle(handle): raise ctypes.WinError(ctypes.get_last_error())
