import importlib.util
import json
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

repo = Path(SPECPATH).resolve().parents[1]
boundary_spec = importlib.util.spec_from_file_location(
    "torch_runtime_boundary", repo / "scripts" / "torch_runtime_boundary.py"
)
boundary_helper = importlib.util.module_from_spec(boundary_spec)
boundary_spec.loader.exec_module(boundary_helper)
torch_spec = importlib.util.find_spec("torch")
torch_internal_excludes = boundary_helper.internal_test_exclusions(Path(torch_spec.origin).parent)
engine_src = repo / "services" / "engine" / "src"
core_src = repo / "packages" / "engine-core" / "src"
migrations = repo / "services" / "engine" / "migrations"
manifest_path = repo / "docs" / "licenses" / "ffmpeg" / "runtime-manifest.json"
media_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
media_runtime = repo / ".runtime" / "ffmpeg" / media_manifest["runtime_id"]
helper_spec = importlib.util.spec_from_file_location(
    "prepare_media_runtime", repo / "scripts" / "prepare_media_runtime.py"
)
media_helper = importlib.util.module_from_spec(helper_spec)
helper_spec.loader.exec_module(media_helper)
media_helper.verify_runtime(media_runtime, media_manifest)
media_binaries = [
    (str(media_runtime / name), "bin")
    for name in media_manifest["files"]
    if name.startswith("bin/")
]
media_data = [
    (str(media_runtime / "LICENSE.txt"), "licenses/ffmpeg"),
    (str(media_runtime / "licenses"), "licenses/ffmpeg"),
    (str(repo / "model-registry" / "demucs-htdemucs.json"), "model-registry"),
    (str(repo / "model-registry" / "LICENSE-demucs.txt"), "model-registry"),
    (str(repo / "model-registry" / "LICENSE-faster-whisper.txt"), "model-registry"),
]


def runtime_module(name):
    return not any(part in {"tests", "test", "testing"} for part in name.split("."))


# The torch/torchaudio hooks include their native CPU libraries. These hidden
# modules are needed by Demucs checkpoint unpickling and its lazy inference path.
audio_hidden = [
    "torch",
    "torchaudio",
    "soundfile",
    "_soundfile_data",
    # The official checkpoint pickles a scalar using NumPy 1's public namespace.
    # NumPy 2 preserves it lazily, so static import analysis cannot discover it.
    "numpy.core.multiarray",
]
audio_hidden += list(boundary_helper.REQUIRED_INTERNAL_MODULES)
for package in ("demucs", "dora", "omegaconf", "openunmix", "julius", "einops"):
    audio_hidden += collect_submodules(package, filter=runtime_module)
audio_data = (
    collect_data_files("demucs")
    + copy_metadata("demucs", recursive=True)
    + copy_metadata("soundfile", recursive=True)
    + copy_metadata("scipy")
)
audio_binaries = collect_dynamic_libs("torch") + collect_dynamic_libs("torchaudio")

migration_data = [
    (str(path), str(path.parent.relative_to(migrations.parent)))
    for path in migrations.rglob("*")
    if path.is_file() and "__pycache__" not in path.parts and path.suffix in {".py", ".mako"}
]

hidden_imports = (
    collect_submodules("ai4music_core")
    + collect_submodules("ai4music_engine")
    + collect_submodules("sqlalchemy.dialects.sqlite")
    + collect_submodules("uvicorn")
    + audio_hidden
)

a = Analysis(
    [str(engine_src / "ai4music_engine" / "launcher.py")],
    pathex=[str(engine_src), str(core_src)],
    binaries=media_binaries + audio_binaries,
    datas=migration_data + media_data + audio_data,
    hiddenimports=hidden_imports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "ai4music_core.lyrics.worker",
        "ai4music_core.lyrics.enhanced",
        "faster_whisper",
        "ctranslate2",
        "av",
        "tokenizers",
        "transformers",
        "safetensors",
        "onnxruntime",
        "huggingface_hub",
        "boto3",
        "botocore",
        "hypothesis",
        "mypy",
        "psycopg",
        "pytest",
        "redis",
        "tensorflow",
        "tkinter",
        # Checkpoint needs logging_tensor; Transformers.generate also imports
        # FSDP's fake_pg helper. Keep those two plus empty parent namespaces.
        *torch_internal_excludes,
        "torch._numpy.testing",
        "torch.fx.passes.tests",
    ],
    noarchive=False,
)
# PyInstaller scans the standalone FFmpeg executables and rediscovers their
# manifest-pinned DLLs at the runtime root. Windows loads the private copies
# next to ffmpeg.exe and ffprobe.exe; keep only those copies.
media_dlls = {
    Path(name).name: (media_runtime / name).resolve()
    for name in media_manifest["files"]
    if name.startswith("bin/") and name.endswith(".dll")
}
a.binaries = [
    entry
    for entry in a.binaries
    if entry[0] not in media_dlls or Path(entry[1]).resolve() != media_dlls[entry[0]]
]
# Hook-provided Torch source files also contain test helpers. They are unrelated
# to inference and must not enter the release payload as loose source/data.
a.datas = [
    entry
    for entry in a.datas
    if not any(part in {"test", "tests"} for part in Path(entry[0]).parts)
    and boundary_helper.keep_torch_runtime_source(entry[0])
    and "torch/_numpy/testing/" not in entry[0].replace("\\", "/")
]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ai4music-engine",
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ai4music-engine",
)
