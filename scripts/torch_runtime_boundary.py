"""Build-time allowlist for Torch 2.5 checkpoint and FSDP runtime helpers."""

from __future__ import annotations

from pathlib import Path

REQUIRED_INTERNAL_MODULES = (
    "torch.testing._internal",
    "torch.testing._internal.logging_tensor",
    "torch.testing._internal.distributed",
    "torch.testing._internal.distributed.fake_pg",
)
_RUNTIME_SOURCES = (
    "torch/testing/_internal/__init__.py",
    "torch/testing/_internal/logging_tensor.py",
    "torch/testing/_internal/distributed/__init__.py",
    "torch/testing/_internal/distributed/fake_pg.py",
)


def internal_test_exclusions(torch_root: Path) -> list[str]:
    """Exclude every other internal child, without importing any test helpers."""
    root = torch_root / "testing" / "_internal"
    exclusions = []

    def visit(directory: Path, prefix: str) -> None:
        for path in directory.iterdir():
            if path.name in {"__pycache__", "__init__.py"}:
                continue
            if not path.is_dir() and path.suffix != ".py":
                continue
            name = path.name if path.is_dir() else path.stem
            module = f"{prefix}.{name}"
            if module in REQUIRED_INTERNAL_MODULES:
                if path.is_dir():
                    visit(path, module)
            else:
                exclusions.append(module)

    visit(root, "torch.testing._internal")
    return sorted(exclusions)


def keep_torch_runtime_source(destination: str) -> bool:
    normalized = destination.replace("\\", "/")
    return "torch/testing/_internal/" not in normalized or any(
        normalized.endswith(source) for source in _RUNTIME_SOURCES
    )
