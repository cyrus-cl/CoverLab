# CoverLab 第三方软件声明

本文件覆盖 CoverLab 0.9.0 Windows CPU 安装包中直接使用或随包分发的主要第三方组件。精确依赖版本以 `uv.lock`、`apps/desktop/package-lock.json` 与 `apps/desktop/src-tauri/Cargo.lock` 为准；各项目仍受其原始许可证约束。本版使用本机分离和公共云端歌词服务。

## 模型与用户数据边界

音频模型权重不包含在安装包。用户明确启动分轨处理后，引擎才会从官方来源下载固定版本的 Demucs HTDemucs 权重到用户模型缓存，并先核对完整 SHA256 再加载。来源、版本、84,141,911 字节大小、MIT 许可与真实哈希记录在随包的 `model-registry/demucs-htdemucs.json` 和 `model-registry/LICENSE-demucs.txt`。安装包不包含用户音频、项目数据库、访问令牌、测试输入、开发路径或云服务凭据。

用户确认上传后，应用将分离人声转换为 16 kHz 单声道音频，通过 CoverLab 的 Cloudflare Worker 转发至百炼临时存储。百炼临时链接有效期为 48 小时。转写通过 `qwen3-asr-flash-filetrans` 获取歌词和时间戳，保存为可编辑的 JSON、LRC 与 SRT。Worker 保管调用密钥，并按音频长度预留公共额度。桌面端使用服务地址与随机任务编号。详见[百炼转写与临时上传说明](https://help.aliyun.com/zh/model-studio/qwen-asr-api-reference)。

Windows 安装包包含 FFmpeg / ffprobe 与所需共享 DLL、Demucs、PyTorch CPU、torchaudio CPU 和 SciPy。首次分轨需要下载固定权重，后续复用本机缓存。

## 桌面与前端

| 组件 | 本版本 | 许可证 | 项目地址 |
| --- | ---: | --- | --- |
| Tauri / Tauri API | 2.12.x | Apache-2.0 OR MIT | <https://github.com/tauri-apps/tauri> |
| Tauri Dialog Plugin | 2.8.4 | MIT OR Apache-2.0 | <https://github.com/tauri-apps/plugins-workspace> |
| Tauri Opener Plugin | 2.7.2 | MIT OR Apache-2.0 | <https://github.com/tauri-apps/plugins-workspace> |
| React / React DOM | 19.3.1 | MIT | <https://github.com/facebook/react> |
| TanStack Query | 5.105.0 | MIT | <https://github.com/TanStack/query> |
| Lucide React | 1.64.0 | ISC | <https://github.com/lucide-icons/lucide> |
| Radix Slider / Radio Group | 1.6.1 / 1.5.1 | MIT | <https://github.com/radix-ui/primitives> |

MediaPipe Tasks Vision 0.10.32 与 Face Landmarker float16 revision 1（3,758,596 字节）随前端打包，仅用于摄像头头部跟随；运行脚本、WASM 和模型来自固定官方来源。Apache-2.0 许可与 NOTICE 位于前端 `tracking/`，完整版本/来源/哈希记录在 `docs/licenses/mediapipe/`。这些视觉模型与按需下载的音频模型分别管理。

OpenCC-JS 1.4.2 用于用户主动选择的繁简转换（程序 MIT，词典数据 Apache-2.0）。原始许可和第三方数据声明随前端 `licenses/opencc-js/` 分发，项目来源 <https://github.com/nk2028/opencc-js>。转换不会按歌词语义补写原文。

## 本机引擎与运行时

| 组件 | 本版本 | 许可证 | 项目地址 |
| --- | ---: | --- | --- |
| CPython | 3.12.15 | PSF-2.0 | <https://www.python.org/> |
| FastAPI | 0.162.0 | MIT | <https://github.com/fastapi/fastapi> |
| Starlette | 1.10.0 | BSD-3-Clause | <https://github.com/Kludex/starlette> |
| Uvicorn | 0.54.0 | BSD-3-Clause | <https://github.com/Kludex/uvicorn> |
| SQLAlchemy | 2.1.6 | MIT | <https://github.com/sqlalchemy/sqlalchemy> |
| Alembic | 1.20.1 | MIT | <https://github.com/sqlalchemy/alembic> |
| Pydantic | 2.14.2 | MIT | <https://github.com/pydantic/pydantic> |
| pydantic-settings | 2.16.2 | MIT | <https://github.com/pydantic/pydantic-settings> |
| platformdirs | 4.14.0 | MIT | <https://github.com/tox-dev/platformdirs> |
| structlog | 25.5.0 | MIT OR Apache-2.0 | <https://github.com/hynek/structlog> |
| AnyIO | 4.15.1 | MIT | <https://github.com/agronholm/anyio> |
| Mako | 1.4.3 | MIT | <https://github.com/sqlalchemy/mako> |
| python-dotenv | 1.2.4 | BSD-3-Clause | <https://github.com/theskumar/python-dotenv> |
| SQLite | 随 CPython | Public Domain | <https://www.sqlite.org/> |
| FFmpeg / ffprobe | n8.1.3-27-g8dca1586f4，BtbN 2026-10-31 win64 LGPL shared | LGPL-3.0-or-later | <https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2026-10-31-12-59> |
| Demucs | 4.0.1 | MIT | <https://github.com/facebookresearch/demucs/tree/v4.0.1> |
| PyTorch CPU / torchaudio CPU | 2.5.1+cpu | BSD-3-Clause / BSD-2-Clause；附带组件以原始 LICENSE 为准 | <https://github.com/pytorch/pytorch/tree/v2.5.1> / <https://github.com/pytorch/audio/tree/v2.5.1> |
| SoundFile / libsndfile | 0.13.1 / wheel 内附版本 | BSD-3-Clause / LGPL-2.1-or-later | <https://github.com/bastibe/python-soundfile> |
| lameenc | 1.9.0 | LGPL-3.0-or-later | <https://github.com/chrisstaite/lameenc> |
| NumPy / SciPy | 2.5.4 / 1.18.1 | BSD-3-Clause；OpenBLAS、LAPACK、GCC runtime 等附带组件以原始 LICENSE 为准 | <https://github.com/numpy/numpy> / <https://github.com/scipy/scipy> |
| Dora / Julius / Open-Unmix / einops / OmegaConf | 见 uv.lock | MIT / MIT / MIT / MIT / BSD-3-Clause | <https://github.com/facebookresearch/dora> / <https://github.com/adefossez/julius> / <https://github.com/sigsep/open-unmix-pytorch> / <https://github.com/arogozhnikov/einops> / <https://github.com/omry/omegaconf> |

FFmpeg 的原始 LGPLv3 及配套 GPLv3 正文、精确 ZIP URL/SHA256、每个 EXE/DLL 的 SHA256、源代码与构建脚本来源，以及未经改写的 `-version` / `-buildconf` / `-L` 输出位于引擎 `_internal/licenses/ffmpeg/`。它作为独立子进程运行，DLL 保持独立可替换；用户可以替换为接口兼容的修改版本，构建时的哈希校验不限制已安装运行时的替换。对应源代码和构建获取说明见该目录的 `README.md`。Python 依赖的原始 LICENSE/NOTICE 随各 `*.dist-info` 元数据保留，PyTorch 的附带组件许可随 `torch` 包保留。


## 构建与安装

| 组件 | 本版本 | 许可证或分发说明 | 项目地址 |
| --- | ---: | --- | --- |
| PyInstaller | 6.22.3 | GPL-2.0-or-later，附带允许分发非自由程序的 Bootloader Exception | <https://github.com/pyinstaller/pyinstaller> |
| NSIS | 由 Tauri 工具链获取 | zlib/libpng | <https://nsis.sourceforge.io/> |
| Microsoft Edge WebView2 | 系统运行时或安装引导程序 | Microsoft 分发条款 | <https://developer.microsoft.com/microsoft-edge/webview2/> |

许可证标识采用 SPDX 表达。发布前若新增运行时依赖、模型或原生二进制，必须在同一提交中更新本文件并重新执行 Windows bundle 校验。

分轨产物仅代表模型真实执行并生成文件，不构成音质指标或分离效果的测量结论。

歌词识别生成可编辑的机器初稿。歌唱文字和时间边界需要试听校对。单次调用验证只能确认转写链路和结果格式。波形与 BPM 分析在本机执行。

## Radix Primitives

MIT License

Copyright (c) 2022 WorkOS

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

