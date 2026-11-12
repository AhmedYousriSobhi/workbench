FROM python:3.12-slim
# Installed at build time, never at container start (issue 235): the exporter starts without network.
ENV PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir pyyaml==6.0.3
COPY exporter.py /app/exporter.py
USER nobody
CMD ["python", "/app/exporter.py", "/inventory.json", "9400"]
