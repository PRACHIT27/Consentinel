# The web app only. The agents deploy separately to Agent Runtime.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY consentinel/ ./consentinel/
COPY web/ ./web/
COPY schema.sql ./

# Cloud Run hands us the port to listen on, and it is not always 8080.
# Reading $PORT rather than hardcoding is the difference between a container
# that starts and one that is killed for failing its health check.
ENV PORT=8080
EXPOSE 8080

# One worker. Firestore calls are I/O-bound and Cloud Run scales by adding
# containers, so extra workers inside one container buy nothing and cost memory.
CMD exec uvicorn web.app:app --host 0.0.0.0 --port ${PORT} --workers 1
