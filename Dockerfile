# Use official Python image
FROM python:3.10-slim

WORKDIR /app

# Install system dependencies, including poppler-utils
RUN apt-get update && apt-get install -y poppler-utils

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Expose port 8080 for Cloud Run
ENV PORT 8080

CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 app:app
