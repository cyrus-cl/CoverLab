#[cfg(windows)]
mod platform {
    use std::ffi::c_void;
    use std::io;
    use std::mem::size_of;
    use std::os::windows::io::AsRawHandle;
    use std::process::Child;

    use windows::Win32::Foundation::{CloseHandle, HANDLE};
    use windows::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
        SetInformationJobObject,
    };

    pub struct WindowsJob {
        handle: HANDLE,
    }

    // A Job Object handle is an owned kernel handle. Moving ownership to the
    // Tauri event-loop thread does not invalidate it or change its semantics.
    unsafe impl Send for WindowsJob {}

    impl WindowsJob {
        pub fn new() -> io::Result<Self> {
            let handle = unsafe { CreateJobObjectW(None, None) }.map_err(io::Error::other)?;
            let mut information = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
            information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;

            let result = unsafe {
                SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    &information as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION as *const c_void,
                    size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                )
            };
            if let Err(error) = result {
                let _ = unsafe { CloseHandle(handle) };
                return Err(io::Error::other(error));
            }

            Ok(Self { handle })
        }

        pub fn assign(&self, child: &Child) -> io::Result<()> {
            let process = HANDLE(child.as_raw_handle());
            unsafe { AssignProcessToJobObject(self.handle, process) }.map_err(io::Error::other)
        }
    }

    impl Drop for WindowsJob {
        fn drop(&mut self) {
            let _ = unsafe { CloseHandle(self.handle) };
        }
    }
}

#[cfg(not(windows))]
mod platform {
    use std::io;
    use std::process::Child;

    pub struct WindowsJob;

    impl WindowsJob {
        pub fn new() -> io::Result<Self> {
            Ok(Self)
        }

        pub fn assign(&self, _child: &Child) -> io::Result<()> {
            Ok(())
        }
    }
}

pub use platform::WindowsJob;
