FROM python:3.11-slim

# opencv-python-headless needs no GUI libraries, but its wheel links libgomp.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./
COPY floorplan/ ./floorplan/

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

ENTRYPOINT ["python", "main.py"]
CMD ["--host", "0.0.0.0", "--port", "8000", "--input", "data/input_images"]
