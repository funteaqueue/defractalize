from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download

REPOSITORY = "lvladikov/SeedVR2-1.4B"
REVISION = os.getenv(
    "SEEDVR_REVISION",
    "7694e0f361dde8521668e9f8e1d242a1ee90035a",
)
COMFY_ROOT = Path("/opt/ComfyUI")
MODEL_ROOT = COMFY_ROOT / "models"
DATA_ROOT = Path("/data")


def materialize(source: str, destination: Path) -> None:
    source_path = Path(source).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() or destination.exists():
        if destination.is_symlink() and destination.resolve() == source_path:
            return
        if destination.is_dir():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    try:
        destination.symlink_to(source_path)
    except OSError:
        shutil.copy2(source_path, destination)


def download(filename: str) -> str:
    return hf_hub_download(
        repo_id=REPOSITORY,
        filename=filename,
        revision=REVISION,
        token=os.getenv("HF_TOKEN") or None,
    )


def main() -> None:
    print(f"Preparing {REPOSITORY}@{REVISION}", flush=True)
    materialize(
        download("comfyui/seedvr2_distill_6L_1.4B_sharp_fp16_comfyui.safetensors"),
        MODEL_ROOT
        / "diffusion_models"
        / "seedvr2_distill_6L_1.4B_sharp_fp16_comfyui.safetensors",
    )
    materialize(
        download("ema_vae_fp16.safetensors"),
        MODEL_ROOT / "vae" / "seedvr2_ema_vae_fp16.safetensors",
    )

    custom_node = COMFY_ROOT / "custom_nodes" / "ComfyUI-SeedVR2-1.4B"
    custom_node.mkdir(parents=True, exist_ok=True)
    materialize(
        download("comfyui/ComfyUI-SeedVR2-1.4B/__init__.py"),
        custom_node / "__init__.py",
    )

    recovery_node = COMFY_ROOT / "custom_nodes" / "defractalize_recovery"
    recovery_node.mkdir(parents=True, exist_ok=True)
    materialize(
        "/bootstrap/recovery_node.py",
        recovery_node / "__init__.py",
    )

    input_directory = DATA_ROOT / "input"
    output_directory = DATA_ROOT / "output"
    temp_directory = DATA_ROOT / "temp"
    for directory in (input_directory, output_directory, temp_directory):
        directory.mkdir(parents=True, exist_ok=True)

    os.chdir(COMFY_ROOT)
    command = [
        sys.executable,
        "main.py",
        "--listen",
        "0.0.0.0",
        "--port",
        "8188",
        "--disable-auto-launch",
        "--input-directory",
        str(input_directory),
        "--output-directory",
        str(output_directory),
        "--temp-directory",
        str(temp_directory),
    ]
    print(f"Starting ComfyUI: {' '.join(command)}", flush=True)
    os.execv(sys.executable, command)


if __name__ == "__main__":
    main()
