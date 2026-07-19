# Alert webhook image (app.py): minimal official base, enough for Flask + influxdb-client.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

# Flask dev server: prototype on the internal Docker network.
CMD ["python", "app.py"]
