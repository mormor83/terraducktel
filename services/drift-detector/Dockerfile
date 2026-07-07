FROM python:3.12-slim

# Note: `software-properties-common` was dropped — it's not in Debian trixie's
# slim image and we don't need it (the image only does `apt install` once for
# the base tooling below, no third-party APT repos).
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl unzip gnupg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ARG TF_VERSION=1.7.5
RUN curl -fsSL "https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_linux_amd64.zip" \
        -o /tmp/tf.zip \
    && unzip /tmp/tf.zip -d /usr/local/bin \
    && rm /tmp/tf.zip \
    && terraform version

RUN groupadd -r appuser && useradd -r -g appuser -m appuser

WORKDIR /app

RUN pip install --no-cache-dir httpx boto3

COPY detector.py .

RUN chown -R appuser:appuser /app

USER appuser

CMD ["python", "detector.py"]
