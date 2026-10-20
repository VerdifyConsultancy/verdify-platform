# Exact-live-base release-only backport for #981. Do not merge this Dockerfile to main.
FROM registry.vallery.net/verdifyconsultancy/verdify-ingestor@sha256:975100356d6899978c4b8145067fac920a67dc7d02db562b474e4e1c7142de32
COPY --chown=1000:1000 ingestor/tasks/ha.py /app/ingestor/tasks/ha.py
COPY --chown=1000:1000 ingestor/shelly_modbus.py /app/ingestor/shelly_modbus.py
