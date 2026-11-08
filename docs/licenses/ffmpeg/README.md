# Bundled FFmpeg runtime

The Windows sidecar distributes the unmodified FFmpeg and ffprobe executables
and all seven libav*/sw* DLLs from BtbN's LGPL shared build. ffplay, headers,
import libraries and development files are omitted. These programs run as
separate processes; the application does not link to their libraries.

- Publisher: https://github.com/BtbN/FFmpeg-Builds
- Pinned release: https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2026-10-31-12-59
- FFmpeg revision: https://github.com/FFmpeg/FFmpeg/tree/8dca1586f468bfcbb78ae3ed618af1361d9f140a
- FFmpeg source archive: https://github.com/FFmpeg/FFmpeg/archive/8dca1586f468bfcbb78ae3ed618af1361d9f140a.tar.gz
- Corresponding build scripts and dependency recipes: https://github.com/BtbN/FFmpeg-Builds/tree/5031c6f1082414e53688d39e521849905071070d
- Build script archive: https://github.com/BtbN/FFmpeg-Builds/archive/5031c6f1082414e53688d39e521849905071070d.tar.gz
- FFmpeg copyright: Copyright (c) 2000-2026 the FFmpeg developers.
- License: GNU Lesser General Public License version 3 or later, as reported by
  this exact build's `ffmpeg -L`. Original LGPLv3 text from the release ZIP is
  `LICENSE.LGPLv3.txt`; the incorporated GPLv3 text is `LICENSE.GPLv3.txt`, copied
  from `COPYING.GPLv3` at the FFmpeg revision above. Including GPLv3's text does
  not change this build's LGPL licensing.

`runtime-manifest.json` records the immutable download URL and SHA256 of the ZIP
and each included file. The downloaded ZIP hash was also matched to the
publisher's release `checksums.sha256`. The October month-end release is chosen
because BtbN documents two-year retention for month-end builds; upstream may
eventually remove it. Keep the reviewed archive available for reproducible
offline builds using `--archive PATH`.

Prepare with `uv run --locked --python 3.12 python scripts/prepare_media_runtime.py`.
The helper verifies hashes before extraction and copies only the reviewed
files into `.runtime/ffmpeg/<runtime_id>/`. `--verify-only` performs no downloads.
The sidecar build includes these files under `_internal/bin/` and this directory
under `_internal/licenses/ffmpeg/`; the ZIP and model weights are never included.
The preparation process also preserves the executable's exact `-version`,
`-buildconf` and `-L` output in this license directory. It does not use an FFmpeg
installation from PATH.

The DLLs remain separate and users may replace them with interface-compatible
modified versions in their own installation. No runtime signature check prevents
this. The original FFmpeg sources and dependency recipes above explain how to
rebuild; `BUILD_CONFIGURATION.txt` records the exact flags. LGPL applies to the
FFmpeg components; other included libraries retain their respective licenses,
as specified in their upstream dependency source trees and build recipes.
