# Exact-live-base release-only backport for #1011/#1022/#1021. Do not merge this Dockerfile to main.
FROM registry.vallery.net/verdifyconsultancy/verdify-ingestor@sha256:9cc29da84f834acdb563d3878d4506a91a3178c1fe505c3697f69db19af25e61
COPY --chown=1000:1000 ingestor/tasks/_common.py /app/ingestor/tasks/_common.py
COPY --chown=1000:1000 ingestor/ai_config.py /app/ingestor/ai_config.py
