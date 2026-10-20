FROM python:3.12-alpine
WORKDIR /app
COPY jellyfin_rename.py /app/jellyfin_rename.py
COPY icon.png /app/store-icon.png
COPY icon.svg /app/store-icon.svg
COPY app-logo.png /app/logo.png
ENV MEDIA_ROOT=/media \
    LOG_PATH=/config/log.jsonl \
    INTERVAL=1800 \
    PORT=8080 \
    PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python3", "/app/jellyfin_rename.py", "--serve"]
