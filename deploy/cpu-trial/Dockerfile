FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 tesseract-ocr tesseract-ocr-rus && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt requirements-local.txt ./
RUN pip install -r requirements-local.txt
COPY src ./src
COPY widget ./widget
COPY migrations ./migrations
COPY alembic.ini ./
RUN useradd --uid 10001 --create-home app && mkdir -p data/raw data/storage logs && chown -R app:app data logs
USER app
CMD ["python", "-m", "uvicorn", "src.core.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
