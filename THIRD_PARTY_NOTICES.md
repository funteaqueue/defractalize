# Third-party components

This repository contains orchestration and UI code. The Docker builds fetch the following upstream
projects and model files; they are not relicensed by this repository.

## GPT Image 2 Artifact Cleaner

- Project: <https://github.com/Larryvrh/gpt-image-2-artifact-cleaner>
- Pinned revision: `5389c6a3bd0eb245ea380bcfcf4c75845709d2da`
- License: PolyForm Noncommercial License 1.0.0
- Important: this component is restricted to noncommercial use unless you obtain separate
  permission from its copyright holder.

## FLUX.2 VAE

- Model: <https://huggingface.co/black-forest-labs/FLUX.2-VAE>
- License: Apache License 2.0, as stated by the artifact-cleaner project.

## SeedVR2-1.4B

- Model: <https://huggingface.co/lvladikov/SeedVR2-1.4B>
- Pinned revision: `7694e0f361dde8521668e9f8e1d242a1ee90035a`
- License: Apache License 2.0
- Based on ByteDance SeedVR2.

## ComfyUI

- Project: <https://github.com/comfyanonymous/ComfyUI>
- Pinned revision: `9cf91339b708a245762fa38ffeec9702b381e0db`
- License: GNU General Public License v3.0

## NVIDIA and PyTorch

The Docker stack requires the NVIDIA Container Toolkit for GPU access. NVIDIA CUDA components and
PyTorch are distributed under their own licenses. Review their terms before redistributing built
container images.

This notice is a practical inventory, not legal advice. Upstream license files and model cards
govern.
