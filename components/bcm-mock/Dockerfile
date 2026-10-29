FROM python:3.12-slim
RUN pip install --no-cache-dir pyyaml
COPY bcm.py /app/bcm.py
USER nobody
CMD ["python", "/app/bcm.py", "/sim.yaml", "8081"]
