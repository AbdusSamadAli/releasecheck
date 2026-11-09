FROM python:3.12-slim

ARG WITH_NODE=true
RUN if [ "$WITH_NODE" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends nodejs npm && rm -rf /var/lib/apt/lists/*; \
    fi

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN python samples/make_samples.py && useradd -m appuser && mkdir -p /app/data/builds /app/data/uploads && chown -R appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1
EXPOSE 8501
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
