# PyAV native runtime provenance

This local CPU preview preserves the unmodified official PyAV 18.1.0 Windows
wheel. `runtime-manifest.json` records its exact wheel URL/SHA256, the original
25 native DLL hashes and PE imports, and the actual FFmpeg library configuration
and license strings obtained by calling the installed DLL APIs.

PyAV source is BSD-3-Clause. The wheel's FFmpeg libraries report LGPLv3 but link
directly to the bundled x264/x265 GPL components. This is a separate stack from
the pinned LGPL command-line FFmpeg in `../ffmpeg/`. No commercial exception has
been purchased, supplied, or inferred. No claim of complete public distribution
compliance or complete corresponding source is made for this preview.

Source and build references:

- PyAV source: https://github.com/PyAV-Org/PyAV/tree/v18.1.0
- PyAV wheel workflow: https://github.com/PyAV-Org/PyAV/tree/v18.1.0/.github/workflows
- Vendor source reference supplied by the upstream maintainer for this exact
  wheel: https://github.com/PyAV-Org/pyav-ffmpeg/tree/8.1.2-1
- FFmpeg source: https://github.com/FFmpeg/FFmpeg/tree/n8.1.2
- x264 source reference: https://code.videolan.org/videolan/x264/-/tree/b35605ace3ddf7c1a5d67a2eb553f034aef41d55
- x265 source: https://bitbucket.org/multicoreware/x265_git/src/master/
- Upstream discussion of the exact 18.1.0 Windows wheel and licensing boundary:
  https://github.com/PyAV-Org/PyAV/issues/2270

These are source references, not an independently verified complete matching
source/build-materials archive. The vendor reference and exception terms require
separate confirmation before public redistribution. The x265 COPYING text is
retained from https://raw.githubusercontent.com/videolan/x265/master/COPYING;
the x264 COPYING text is retained from the exact-revision mirror at
https://raw.githubusercontent.com/mirror/x264/b35605ace3ddf7c1a5d67a2eb553f034aef41d55/COPYING.
PyAV's exact BSD license is copied
from its installed wheel metadata. LGPLv3 text for FFmpeg is retained in
`../ffmpeg/` and runtime distribution metadata.
