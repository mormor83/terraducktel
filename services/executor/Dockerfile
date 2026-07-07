FROM hashicorp/terraform:1.10

# Install Python for checkov + git/jq for executor lifecycle scripting,
# plus unzip for runtime-downloaded terraform binaries (terraducktel.yaml
# version override; see entrypoint.sh "Setting Version" step).
#
# `apk upgrade` first so expat in the base layers matches python3.12's
# pyexpat.so (Alpine 3.21 ships pyexpat built against expat 2.7+ which
# requires XML_SetAllocTrackerActivationThreshold — without the upgrade
# pip itself fails to import on the hashicorp/terraform:1.10 image).
RUN apk upgrade --no-cache \
    && apk add --no-cache python3 py3-pip curl wget bash git jq unzip

# Install yq (mike-farah) for parsing terraducktel.yaml at runtime.
ARG YQ_VERSION=4.44.3
RUN wget -q "https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_amd64" \
    -O /usr/local/bin/yq \
    && chmod +x /usr/local/bin/yq \
    && yq --version

# Install checkov (pinned for reproducible security gates)
ARG CHECKOV_VERSION=3.2.395
RUN pip3 install --no-cache-dir checkov==${CHECKOV_VERSION} --break-system-packages

# Install trivy (pinned version for reproducibility)
ARG TRIVY_VERSION=0.70.0
RUN wget -q "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz" \
    -O /tmp/trivy.tar.gz \
    && tar -xzf /tmp/trivy.tar.gz -C /usr/local/bin trivy \
    && rm /tmp/trivy.tar.gz \
    && trivy --version

# Install infracost CLI (cost estimation step). Static Go binary, works on
# Alpine. Pinned for reproducibility.
ARG INFRACOST_VERSION=0.10.39
RUN wget -q "https://github.com/infracost/infracost/releases/download/v${INFRACOST_VERSION}/infracost-linux-amd64.tar.gz" \
    -O /tmp/infracost.tar.gz \
    && tar -xzf /tmp/infracost.tar.gz -C /tmp \
    && mv /tmp/infracost-linux-amd64 /usr/local/bin/infracost \
    && chmod +x /usr/local/bin/infracost \
    && rm /tmp/infracost.tar.gz \
    && infracost --version

# Install conftest (OPA policy gate). Static Go binary, pinned. Powers the
# "OPA Policy Check" step (entrypoint.sh) and matches the version the API image
# uses for the in-app test/verify loop.
ARG CONFTEST_VERSION=0.56.0
RUN wget -q "https://github.com/open-policy-agent/conftest/releases/download/v${CONFTEST_VERSION}/conftest_${CONFTEST_VERSION}_Linux_x86_64.tar.gz" \
    -O /tmp/conftest.tar.gz \
    && tar -xzf /tmp/conftest.tar.gz -C /usr/local/bin conftest \
    && rm /tmp/conftest.tar.gz \
    && conftest --version

# Bundled default policies (the repo's policies/*.rego). The OPA step includes
# these when the BU's opa.use_bundled is true. Build context is the repo root
# (see docker-compose.yml) so this path resolves.
COPY policies/ /opt/tdt/policies/bundled/

# Create non-root user
RUN adduser -D -u 1001 executor

WORKDIR /workspace

RUN chown executor:executor /workspace

COPY services/executor/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER executor

ENTRYPOINT ["/entrypoint.sh"]
