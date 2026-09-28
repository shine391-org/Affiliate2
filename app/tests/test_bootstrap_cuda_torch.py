# SPDX-License-Identifier: AGPL-3.0-only
"""The bootstrap CUDA-torch policy must be correct on every machine shape."""
import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "affiliate2_cuda_torch", _ROOT / "scripts" / "cuda_torch.py"
)
cuda_torch = importlib.util.module_from_spec(_spec)
sys.modules["affiliate2_cuda_torch"] = cuda_torch
_spec.loader.exec_module(cuda_torch)


CPU = cuda_torch.TorchReport(torch="2.8.0+cpu", cuda=False, torch_cuda=None)
CUDA = cuda_torch.TorchReport(
    torch="2.8.0+cu129", cuda=True, torch_cuda="12.9", device="RTX 2060 SUPER"
)


def test_cpu_only_torch_on_a_gpu_machine_is_upgraded():
    decision = cuda_torch.decide(system="Windows", gpu_present=True, current=CPU)
    assert decision.apply is True
    assert "no CUDA runtime" in decision.reason


def test_cuda_torch_is_never_reinstalled():
    decision = cuda_torch.decide(system="Windows", gpu_present=True, current=CUDA)
    assert decision.apply is False
    assert "already installed" in decision.reason


def test_machines_without_an_nvidia_gpu_are_left_alone():
    decision = cuda_torch.decide(system="Windows", gpu_present=False, current=CPU)
    assert decision.apply is False
    assert decision.gpu_present is False
    assert "CPU" in decision.reason


def test_non_windows_platforms_are_never_patched():
    for system in ("Linux", "Darwin"):
        decision = cuda_torch.decide(system=system, gpu_present=True, current=CPU)
        assert decision.apply is False
        assert decision.platform_supported is False


def test_a_broken_torch_probe_does_not_trigger_a_reinstall():
    broken = cuda_torch.TorchReport(error="No module named 'torch'")
    decision = cuda_torch.decide(system="Windows", gpu_present=True, current=broken)
    assert decision.apply is False
    assert "probe failed" in decision.reason


def test_pinned_packages_match_the_requirements_lock():
    """A silent version drift here would break the WhisperX/PyTorch pairing."""
    lock = (_ROOT / "app" / "requirements-lock.txt").read_text(encoding="utf-8")
    requirements = (_ROOT / "app" / "requirements.txt").read_text(encoding="utf-8")
    for source in (lock, requirements):
        assert "torch==2.8.0\n" in source or "torch==2.8.0\r\n" in source
        assert "torchaudio==2.8.0" in source
        assert "torchvision==0.23.0" in source
    assert "torch==2.8.0+cu129" in cuda_torch.TORCH_PACKAGES
    assert "torchaudio==2.8.0+cu129" in cuda_torch.TORCH_PACKAGES
    assert "torchvision==0.23.0+cu129" in cuda_torch.TORCH_PACKAGES


def test_install_command_does_not_let_pip_re_resolve_the_stack():
    ok, command = cuda_torch.install_cuda_torch("python", dry_run=True)
    assert ok is True
    assert "--no-deps" in command
    assert cuda_torch.CUDA_INDEX in command
    assert "-r" not in command


def test_describe_reports_a_probe_failure_instead_of_raising():
    text = cuda_torch.describe(cuda_torch.TorchReport(error="boom"))
    assert "boom" in text
    assert "RTX" in cuda_torch.describe(CUDA)
