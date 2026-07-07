FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl git unzip \
    && rm -rf /var/lib/apt/lists/*

# AWS CLI v2 — EKS kubeconfigs authenticate via `aws eks get-token` (exec
# plugin), so the /clusters/{id}/test endpoint needs the aws CLI on PATH.
RUN ARCH=$(dpkg --print-architecture) \
    && case "$ARCH" in arm64) A=aarch64;; *) A=x86_64;; esac \
    && curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${A}.zip" -o /tmp/awscliv2.zip \
    && unzip -q /tmp/awscliv2.zip -d /tmp \
    && /tmp/aws/install \
    && rm -rf /tmp/aws /tmp/awscliv2.zip \
    && aws --version

# kubectl — used by the /api/v1/clusters/{id}/test endpoint to probe a cluster's
# reachability with the saved kubeconfig. Tracks the cluster's minor (EKS 1.35);
# resolved to the latest patch of that line at build so it stays within the
# kubectl/apiserver version-skew window. Bump KUBECTL_MINOR when clusters upgrade.
ARG KUBECTL_MINOR=1.35
RUN ARCH=$(dpkg --print-architecture) \
    && KVER=$(curl -fsSL "https://dl.k8s.io/release/stable-${KUBECTL_MINOR}.txt") \
    && curl -fsSL -o /usr/local/bin/kubectl "https://dl.k8s.io/release/${KVER}/bin/linux/${ARCH}/kubectl" \
    && chmod +x /usr/local/bin/kubectl \
    && kubectl version --client=true

# conftest (OPA policy engine) — powers the synchronous policy test/verify
# endpoints (POST /v1/policies/test|verify). Pinned to match the executor image
# so the in-app authoring loop produces identical results to real runs.
ARG CONFTEST_VERSION=0.56.0
RUN ARCH=$(dpkg --print-architecture) \
    && case "$ARCH" in arm64) A=arm64;; *) A=x86_64;; esac \
    && curl -fsSL "https://github.com/open-policy-agent/conftest/releases/download/v${CONFTEST_VERSION}/conftest_${CONFTEST_VERSION}_Linux_${A}.tar.gz" -o /tmp/conftest.tar.gz \
    && tar -xzf /tmp/conftest.tar.gz -C /usr/local/bin conftest \
    && rm /tmp/conftest.tar.gz \
    && conftest --version

RUN groupadd -r terraducktel && useradd -r -g terraducktel -d /app -s /bin/false terraducktel

WORKDIR /app

COPY pyproject.toml requirements.lock .
# requirements.lock is generated from uv.lock (`uv export --frozen --extra dev
# --no-emit-project -o requirements.lock`) so every dependency — including
# transitive ones — resolves to the exact version+hash pinned in uv.lock
# instead of whatever `>=` ranges happen to resolve on build day. Re-run that
# command (after `uv lock`) whenever pyproject.toml's dependencies change.
RUN pip install --no-cache-dir -r requirements.lock \
    && pip install --no-cache-dir --no-deps -e .

COPY app/ ./app/
COPY alembic.ini .
COPY alembic/ ./alembic/
COPY scripts/ ./scripts/
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

RUN chown -R terraducktel:terraducktel /app

USER terraducktel

EXPOSE 8000

# Entrypoint runs `alembic upgrade head` (idempotent), optionally seeds dev
# users when TDT_BOOTSTRAP_SEED_USERS=true, then execs uvicorn. See
# docker-entrypoint.sh for the toggles.
CMD ["/usr/local/bin/docker-entrypoint.sh"]
