#!/usr/bin/env python3
"""Install a CUDA-capable PyTorch build on Windows + NVIDIA machines.

Why this exists
---------------
`app/requirements-lock.txt` pins `torch==2.8.0` and friends. PyPI ships a
CPU-only wheel for those pins on Windows, so a plain `pip install -r
requirements-lock.txt` produced a `2.8.0+cpu` build and every GPU stage
(Demucs HQ separation, WhisperX alignment) silently fell back to CPU. The
official CUDA wheels for the very same versions live on the PyTorch index and
are only reachable through `--index-url`.

What this guarantees
--------------------
- The CUDA wheel family matches the pins exactly (no version drift).
- Non-Windows and CPU-only machines are left untouched.
- The rest of the lock is installed by pip first, so the CUDA step only swaps
  three already-satisfied packages and never lets the resolver downgrade the
  wider stack (`--no-deps`).
- Failure is reported, not fatal: Affiliate2 still runs on CPU, it just reports
  that HQ separation will be slow.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Sequence

# The pinned family from app/requirements.txt / app/requirements-lock.txt.
# cu129 is the only official CUDA build that publishes all three of these exact
# versions for cp311/win_amd64, and it matches the nvidia-*-cu12 12.9 runtime
# wheels already pinned for CTranslate2.
CUDA_INDEX = "https://download.pytorch.org/whl/cu129"
CUDA_TAG = "cu129"
TORCH_PACKAGES = (
    "torch==2.8.0+cu129",
    "torchaudio==2.8.0+cu129",
    "torchvision==0.23.0+cu129",
)

_PROBE = (
    "import json, torch;"
    "print(json.dumps({"
    "'torch': torch.__version__,"
    "'cuda': bool(torch.cuda.is_available()),"
    "'torch_cuda': torch.version.cuda,"
    "'device': (torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)}))"
)


@dataclass
class TorchReport:
    """Observed PyTorch build in the target interpreter."""

    torch: str | None = None
    cuda: bool = False
    torch_cuda: str | None = None
    device: str | None = None
    error: str | None = None

    @property
    def cuda_ready(self) -> bool:
        return self.cuda and self.torch_cuda is not None


@dataclass
class Decision:
    """What the installer intends to do and why."""

    apply: bool
    reason: str
    platform_supported: bool
    gpu_present: bool
    current: TorchReport = field(default_factory=TorchReport)


def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)


def torch_report(python_exe: str | None = None) -> TorchReport:
    """Report the installed PyTorch build without raising."""
    exe = python_exe or sys.executable
    result = _run([exe, "-c", _PROBE])
    if result.returncode != 0:
        return TorchReport(error=(result.stderr or result.stdout or "").strip()[-400:])
    try:
        payload = json.loads((result.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:  # pragma: no cover - defensive
        return TorchReport(error=f"unparseable probe output: {exc}")
    return TorchReport(
        torch=payload.get("torch"),
        cuda=bool(payload.get("cuda")),
        torch_cuda=payload.get("torch_cuda"),
        device=payload.get("device"),
    )


def nvidia_gpu_present() -> bool:
    """True when an NVIDIA GPU and driver are usable from this OS."""
    system = platform.system()
    if system == "Windows":
        smi = shutil.which("nvidia-smi")
        if not smi:
            return False
        result = _run([smi, "-L"])
        return result.returncode == 0 and "NVIDIA" in (result.stdout or "")
    if system == "Linux":
        if not shutil.which("nvidia-smi"):
            return False
        return _run(["nvidia-smi", "-L"]).returncode == 0
    return False


def decide(
    *,
    system: str | None = None,
    gpu_present: bool | None = None,
    current: TorchReport | None = None,
) -> Decision:
    """Pure decision function so the policy is testable without a GPU."""
    system = system if system is not None else platform.system()
    current = current if current is not None else TorchReport()
    if gpu_present is None:
        gpu_present = nvidia_gpu_present()

    if system != "Windows":
        return Decision(
            apply=False,
            reason=(
                f"{system} is not the supported CUDA-wheel path; the pinned PyPI "
                "torch build is used as-is."
            ),
            platform_supported=False,
            gpu_present=gpu_present,
            current=current,
        )
    if not gpu_present:
        return Decision(
            apply=False,
            reason="No NVIDIA GPU detected; HQ separation will run on CPU.",
            platform_supported=True,
            gpu_present=False,
            current=current,
        )
    if current.error:
        return Decision(
            apply=False,
            reason=f"PyTorch probe failed: {current.error}",
            platform_supported=True,
            gpu_present=True,
            current=current,
        )
    if current.cuda_ready:
        return Decision(
            apply=False,
            reason=f"CUDA torch already installed ({current.torch}, {current.torch_cuda}).",
            platform_supported=True,
            gpu_present=True,
            current=current,
        )
    return Decision(
        apply=True,
        reason=(
            f"Installed torch {current.torch} has no CUDA runtime; installing the "
            f"pinned CUDA build from {CUDA_INDEX}."
        ),
        platform_supported=True,
        gpu_present=True,
        current=current,
    )


def install_cuda_torch(
    python_exe: str | None = None,
    *,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """Install the pinned CUDA trio. Returns (ok, detail)."""
    exe = python_exe or sys.executable
    cmd = [
        exe, "-m", "pip", "install",
        # The trio is already present (CPU build) with all of its dependencies
        # satisfied by the lock install. --no-deps keeps pip from re-resolving
        # the wider stack, which is how silent downgrades happen.
        "--no-deps", "--upgrade",
        "--index-url", CUDA_INDEX,
        *TORCH_PACKAGES,
    ]
    if dry_run:
        return True, " ".join(cmd)
    env = dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK="1")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
    if result.returncode != 0:
        return False, ((result.stderr or "") or (result.stdout or ""))[-1500:]
    return True, (result.stdout or "").strip()[-1500:]


def describe(report: TorchReport) -> str:
    if report.error:
        return f"probe failed: {report.error}"
    return (
        f"torch={report.torch} cuda={report.cuda} "
        f"torch.version.cuda={report.torch_cuda} gpu={report.device}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=None, help="Interpreter to inspect/patch")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report the decision and current build without installing.",
    )
    args = parser.parse_args(argv)

    current = torch_report(args.python)
    decision = decide(current=current)
    print(f"[torch] current: {describe(current)}")
    print(f"[torch] decision: {decision.reason}")

    if not decision.apply or args.check:
        return 0

    ok, detail = install_cuda_torch(args.python)
    if not ok:
        print(f"[torch] WARNING: CUDA torch install failed:\n{detail}")
        print("[torch] Affiliate2 will run; HQ Demucs separation falls back to CPU.")
        return 0

    after = torch_report(args.python)
    print(f"[torch] installed from {CUDA_INDEX} ({CUDA_TAG})")
    print(f"[torch] now: {describe(after)}")
    if not after.cuda_ready:
        print("[torch] WARNING: CUDA build installed but no CUDA device is visible.")
        return 0
    print(f"[torch] CUDA OK: {after.device}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
