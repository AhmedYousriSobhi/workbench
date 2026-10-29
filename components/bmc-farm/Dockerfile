FROM python:3.12-slim
RUN pip install --no-cache-dir pyyaml
COPY bmc.py /app/bmc.py
USER nobody
CMD ["python", "/app/bmc.py", "/sim.yaml", "8000"]
