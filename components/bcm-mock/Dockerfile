FROM python:3.12-slim
COPY bcm.py /app/bcm.py
USER nobody
CMD ["python", "/app/bcm.py", "/inventory.json", "8081"]
