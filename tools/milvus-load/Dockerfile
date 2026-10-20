# Milvus yük üreticisi (chaos senaryoları için; yalnızca GKE test tenant'ı). Derleme: make load-image
FROM python:3.12.14-slim
RUN pip install --no-cache-dir pymilvus==2.6.17 numpy==2.3.3
COPY load.py /app/load.py
USER 65532:65532
ENTRYPOINT ["python", "-u", "/app/load.py"]
