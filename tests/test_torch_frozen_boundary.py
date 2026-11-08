from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def helper():
    spec = importlib.util.spec_from_file_location(
        "torch_runtime_boundary", ROOT / "scripts" / "torch_runtime_boundary.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checkpoint_and_fsdp_helpers_define_the_internal_package_boundary() -> None:
    import torch

    policy = helper()
    assert set(policy.REQUIRED_INTERNAL_MODULES) == {
        "torch.testing._internal",
        "torch.testing._internal.logging_tensor",
        "torch.testing._internal.distributed",
        "torch.testing._internal.distributed.fake_pg",
    }
    exclusions = policy.internal_test_exclusions(Path(torch.__file__).parent)
    assert "torch.testing._internal" not in exclusions
    assert "torch.testing._internal.logging_tensor" not in exclusions
    assert "torch.testing._internal.common_utils" in exclusions
    assert "torch.testing._internal.jit_utils" in exclusions
    assert "torch.testing._internal.common_device_type" in exclusions
    assert "torch.testing._internal.distributed" not in exclusions
    assert "torch.testing._internal.distributed.fake_pg" not in exclusions
    assert "torch.testing._internal.distributed.rpc_utils" in exclusions


def test_only_checkpoint_logging_sources_can_cross_internal_data_boundary() -> None:
    policy = helper()
    assert policy.keep_torch_runtime_source("torch/testing/_internal/__init__.py")
    assert policy.keep_torch_runtime_source(r"torch\testing\_internal\logging_tensor.py")
    assert not policy.keep_torch_runtime_source("torch/testing/_internal/common_utils.py")
    assert not policy.keep_torch_runtime_source("torch/testing/_internal/distributed/rpc_utils.py")
    assert policy.keep_torch_runtime_source("torch/testing/_internal/distributed/fake_pg.py")
    assert policy.keep_torch_runtime_source("torch/utils/checkpoint.py")
