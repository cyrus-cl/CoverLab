/// Respect the product's black/white native titlebar independently of the OS accent.
#[cfg(target_os = "windows")]
pub fn apply(window: &tauri::WebviewWindow) {
    use std::ffi::c_void;
    use windows::Win32::Foundation::HWND;
    use windows::Win32::Graphics::Dwm::{
        DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR, DWMWA_USE_IMMERSIVE_DARK_MODE, DwmSetWindowAttribute,
    };
    let _ = window.set_theme(Some(tauri::Theme::Dark));
    if let Ok(native) = window.hwnd() {
        let hwnd = HWND(native.0);
        // Caption/text colours are available on Windows 11. Older versions retain
        // the immersive dark titlebar if those optional attributes are unsupported.
        for (attribute, value) in [
            (DWMWA_USE_IMMERSIVE_DARK_MODE, 1_u32),
            (DWMWA_CAPTION_COLOR, 0x000000_u32),
            (DWMWA_TEXT_COLOR, 0xFFFFFF_u32),
        ] {
            unsafe {
                let _ = DwmSetWindowAttribute(
                    hwnd,
                    attribute,
                    (&value as *const u32).cast::<c_void>(),
                    std::mem::size_of::<u32>() as u32,
                );
            }
        }
    }
}

#[cfg(not(target_os = "windows"))]
pub fn apply(_window: &tauri::WebviewWindow) {}
