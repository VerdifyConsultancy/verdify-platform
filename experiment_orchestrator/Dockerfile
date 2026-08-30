# syntax=docker/dockerfile:1.7
# One non-actuating image; Kubernetes selects lifecycle, selector, or freezer.
FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS builder

WORKDIR /build
COPY experiment_orchestrator/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin orchestrator

WORKDIR /app
COPY --from=builder /install /usr/local
# Copy source files explicitly. Local test caches and bytecode can never enter
# the image even when the root build context is dirty.
COPY experiment_orchestrator/*.py /app/experiment_orchestrator/
# The evaluator source itself is part of the endpoint identity. Copy the exact
# locked implementation under an image-local name so the runtime hashes the
# same immutable bytes that design lock names.
COPY research/planner-efficacy/switchback/v2_outcomes.py /app/experiment_orchestrator/_frozen_v2_outcomes.py
# The attended direct-proof Job reuses this non-device runtime.  Carry only its
# source-stamped, read-only collector/guard surfaces and the exact source files
# whose hashes the readiness packet binds; none imports a device client.
COPY scripts/experiment_v2_proof_packet.py scripts/experiment_v2_readiness_guard.py scripts/mcp-security-acceptance.py /app/scripts/
COPY scripts/verify_component_proof_packet.py /app/readiness-source/scripts/verify_component_proof_packet.py
COPY research/planner-efficacy/switchback/v2_selector.py /app/readiness-source/research/planner-efficacy/switchback/v2_selector.py
COPY research/planner-efficacy/switchback/v2_outcomes.py /app/readiness-source/research/planner-efficacy/switchback/v2_outcomes.py
COPY research/planner-efficacy/protocols/shadow-v2/selector-artifact-v1.json /app/readiness-source/research/planner-efficacy/protocols/shadow-v2/selector-artifact-v1.json
COPY ingestor/tasks/component_experiment.py /app/readiness-source/ingestor/tasks/component_experiment.py
COPY deploy/k8s/components/hermes-iris/hermes-config.yaml /app/readiness-source/deploy/k8s/components/hermes-iris/hermes-config.yaml
COPY tests/fixtures/experiment-v2-readiness/base-proof.json /app/readiness-source/tests/fixtures/experiment-v2-readiness/base-proof.json
# The managed build clone uses umask 077. Docker/kaniko preserve those source
# modes on bare COPY, so normalize every runtime module before dropping root;
# otherwise uid 10001 cannot import a freshly built image.
RUN find /app -type d -exec chmod 0555 {} + \
    && find /app -type f -exec chmod 0444 {} +

ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ARG GIT_REF=unknown
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VERDIFY_GIT_SHA=$GIT_SHA \
    VERDIFY_BUILD_TIME=$BUILD_TIME \
    VERDIFY_GIT_REF=$GIT_REF
# Reserved revision/created OCI labels are supplied by the managed builder.
LABEL org.opencontainers.image.source="https://github.com/VerdifyConsultancy/verdify-platform" \
      org.opencontainers.image.title="verdify-experiment-v2-orchestrator"

USER 10001:10001
ENTRYPOINT ["python", "-m", "experiment_orchestrator"]
