FROM python:3.12-slim

COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker

ARG KUBECTL_VERSION=v1.31.4

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/opt/researchforge/backend

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates curl git postgresql-client \
    && curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && curl -fsSLo /tmp/kubectl.sha256 "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl.sha256" \
    && echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum --check \
    && chmod +x /usr/local/bin/kubectl \
    && rm -f /tmp/kubectl.sha256 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/researchforge

COPY backend/requirements.txt /opt/researchforge/backend/requirements.txt
RUN pip install -r /opt/researchforge/backend/requirements.txt

COPY backend/app /opt/researchforge/backend/app
COPY backend/scripts /opt/researchforge/backend/scripts
COPY backend/migrations /opt/researchforge/backend/migrations
COPY benchmarks /opt/researchforge/benchmarks

WORKDIR /opt/researchforge/backend
EXPOSE 8001

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers ${RESEARCHFORGE_API_WORKERS:-1}"]
