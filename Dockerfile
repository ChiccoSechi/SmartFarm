# Minimal image: the simulator uses only the Python stdlib for basic execution.
FROM python:3.12-slim

WORKDIR /app

COPY sensor_simulator.py .

# Install paho-mqtt, required by publish_to_mqtt() in sensor_simulator.py.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# No "VOLUME /app/data": it would trigger the anonymous volume of "docker run" that writes the CSV outside ./data; the host mount is in docker-compose.yml (use compose, not docker run).

RUN mkdir -p /app/data

ENTRYPOINT ["python3", "sensor_simulator.py"]
CMD ["--interval", "1", "--csv-path", "data/sensor_data.csv"]
