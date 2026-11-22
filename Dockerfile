FROM denoland/deno:bin-2.9.7 AS deno
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       ca-certificates ffmpeg libgl1 libglib2.0-0 libgomp1 tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 traffic \
    && useradd --uid 10001 --gid 10001 --create-home traffic \
    && install -d -o traffic -g traffic /var/lib/traffic

COPY --from=deno /deno /usr/local/bin/deno
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
       torch==2.12.1 torchvision==0.27.1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY common/ common/
COPY cv/ cv/
COPY dashboard/ dashboard/
COPY data/ data/
RUN pip install --no-cache-dir . && pip check
COPY yolo26n.pt ./

ENV HOME=/home/traffic
USER traffic
