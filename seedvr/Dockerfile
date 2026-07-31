FROM python:3.13-slim-bookworm

ARG COMFYUI_COMMIT=9cf91339b708a245762fa38ffeec9702b381e0db
ARG PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cu130

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
      --index-url "${PYTORCH_INDEX_URL}" \
      torch==2.13.0+cu130 torchvision==0.28.0+cu130 torchaudio==2.11.0+cu130

RUN git clone https://github.com/comfyanonymous/ComfyUI.git /opt/ComfyUI \
    && git -C /opt/ComfyUI checkout --detach "${COMFYUI_COMMIT}" \
    && git -C /opt/ComfyUI remote remove origin \
    && pip install --no-cache-dir -r /opt/ComfyUI/requirements.txt \
    && pip install --no-cache-dir huggingface-hub==1.5.0

WORKDIR /bootstrap
COPY bootstrap.py .
COPY recovery_node.py .

EXPOSE 8188

ENTRYPOINT ["python", "/bootstrap/bootstrap.py"]
