FROM python:3.12-slim AS cert
RUN pip install --no-cache-dir cryptography
COPY gen_cert.py /gen_cert.py
RUN python /gen_cert.py /certs

FROM python:3.12-slim
COPY bmc.py /app/bmc.py
COPY --from=cert --chown=nobody /certs /app/certs
USER nobody
CMD ["python", "/app/bmc.py", "/inventory.json", "8000", "8443"]
