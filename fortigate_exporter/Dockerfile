FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY exporter.py .
COPY config.yaml .

EXPOSE 9200

CMD ["python", "exporter.py"]