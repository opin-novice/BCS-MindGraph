FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY .env .env
COPY src/ src/
COPY data/ data/
COPY static/ static/
COPY runtime/ runtime/
COPY run_pipeline.py .

RUN mkdir -p runtime

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

EXPOSE 8000

CMD ["uvicorn", "bcs.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
