FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./

RUN groupadd --gid 10001 agent \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin agent \
    && mkdir -p /workspace \
    && chown agent:agent /workspace

USER agent
WORKDIR /workspace

ENTRYPOINT ["python", "/app/main.py"]
