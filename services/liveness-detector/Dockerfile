FROM python:3.12-slim

# No OS packages needed — httpx provides TLS roots via certifi (bundled),
# and the detector is a pure-python long-running process. Skipping apt-get
# avoids slow/flaky Debian mirror hits during build.

RUN groupadd -r appuser && useradd -r -g appuser -m appuser

WORKDIR /app

RUN pip install --no-cache-dir httpx

COPY detector.py .

RUN chown -R appuser:appuser /app

USER appuser

CMD ["python", "detector.py"]
