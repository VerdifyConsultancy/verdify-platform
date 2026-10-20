# Release-only S6 backport: preserve every layer of the actual live ingestor.
# Main's ordinary Dockerfile and its unpromoted control changes are untouched.
FROM registry.vallery.net/verdifyconsultancy/verdify-ingestor@sha256:4b4994bc68c9e0966d2665d4d4da3e4e7e5d1c2dc8b2694f95db1fa629dc6000
COPY --chown=1000:1000 ingestor/ingestor.py /app/ingestor/ingestor.py
COPY --chown=1000:1000 ingestor/soil_saturation.py /app/ingestor/soil_saturation.py
