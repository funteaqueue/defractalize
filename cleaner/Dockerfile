FROM python:3.13-slim-bookworm

ARG CLEANER_COMMIT=5389c6a3bd0eb245ea380bcfcf4c75845709d2da
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

RUN git clone https://github.com/Larryvrh/gpt-image-2-artifact-cleaner.git /opt/cleaner \
    && git -C /opt/cleaner checkout --detach "${CLEANER_COMMIT}" \
    && git -C /opt/cleaner remote remove origin \
    && pip install --no-cache-dir /opt/cleaner

WORKDIR /service
COPY requirements-service.txt .
RUN pip install --no-cache-dir -r requirements-service.txt
COPY service.py .

EXPOSE 8001

CMD ["uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
