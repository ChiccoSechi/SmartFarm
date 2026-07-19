FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY consumer_alerts_to_mongo.py .
CMD ["python", "consumer_alerts_to_mongo.py"]
