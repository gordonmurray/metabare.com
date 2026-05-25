FROM python:3.9-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Preload the multivector model at build time so the container starts
# fast and does not need huggingface to be reachable at runtime. The
# model lands in /root/.cache/huggingface and rides inside this layer.
ARG ENCODER_MODEL=vidore/colpali-v1.3
ENV ENCODER_MODEL=$ENCODER_MODEL
RUN python -c "import torch; from colpali_engine.models import ColPali, ColPaliProcessor; \
    ColPali.from_pretrained('$ENCODER_MODEL', torch_dtype=torch.bfloat16); \
    ColPaliProcessor.from_pretrained('$ENCODER_MODEL')"

COPY main.py ./

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
