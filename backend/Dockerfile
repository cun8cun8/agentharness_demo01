ARG KUBECTL_VERSION=v1.37.1
FROM golang:1.27.2-bookworm AS kubectl-build
ARG KUBECTL_VERSION
ENV CGO_ENABLED=0 GOWORK=off
WORKDIR /src/kubernetes
RUN curl -fsSLo /tmp/kubernetes-src.tar.gz "https://dl.k8s.io/${KUBECTL_VERSION}/kubernetes-src.tar.gz" \
    && curl -fsSLo /tmp/kubernetes-src.sha256 "https://dl.k8s.io/${KUBECTL_VERSION}/kubernetes-src.tar.gz.sha256" \
    && echo "$(cat /tmp/kubernetes-src.sha256)  /tmp/kubernetes-src.tar.gz" | sha256sum --check \
    && tar -xzf /tmp/kubernetes-src.tar.gz \
    && go mod edit -require=golang.org/x/net@v0.60.0 \
    && go build -mod=mod -trimpath -ldflags "-X k8s.io/component-base/version.gitVersion=${KUBECTL_VERSION}" -o /out/kubectl ./cmd/kubectl

FROM python:3.12-slim

COPY --from=docker:29.9.0-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=kubectl-build /out/kubectl /usr/local/bin/kubectl

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/opt/researchforge/backend

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates curl git postgresql-client \
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
