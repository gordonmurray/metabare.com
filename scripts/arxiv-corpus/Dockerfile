FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        poppler-utils \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work

COPY requirements.txt /work/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY build_corpus.py /work/build_corpus.py
COPY papers.json /work/papers.json

ENTRYPOINT ["python", "/work/build_corpus.py"]
