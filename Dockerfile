# One connector app per container. Build: docker build -t open-mcp-odoo:<tag> .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install --require-hashes --no-deps -r requirements.lock
COPY pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.md ./
COPY src ./src
RUN pip install --no-deps . && useradd --system --uid 10001 --home /data connector && mkdir -p /data && chown connector /data
USER connector
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
ENTRYPOINT ["open-connectors", "serve", "--host", "0.0.0.0", "--port", "8000"]
CMD ["--config", "/config/connector.toml"]
