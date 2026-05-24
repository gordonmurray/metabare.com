FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work

COPY requirements.txt /work/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY fetch.py /work/fetch.py
COPY photos.json /work/photos.json

ENTRYPOINT ["python", "/work/fetch.py"]
