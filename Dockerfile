# ==============================================================================
# Dockerfile for WealthOS / Family Office Platform
# ==============================================================================

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    curl \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

EXPOSE 8000

ENV TZ=Asia/Kolkata
ENV PYTHONUNBUFFERED=1

CMD ["python", "-u", "main.py"]
