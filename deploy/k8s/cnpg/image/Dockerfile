# TimescaleDB-on-CloudNativePG image (HA-4.1, issue #243).
#
# WHY THIS IMAGE EXISTS
# ---------------------
# CNPG's default operand image (ghcr.io/cloudnative-pg/postgresql:N) is vanilla
# PostgreSQL: it has NO timescaledb extension. The Verdify system-of-record is a
# TimescaleDB (hypertables + native compression on 5 hypertables) at extension
# version **2.25.2** on PG16. To run that workload under CNPG (operator-managed
# failover, sync replica, -rw primary-following endpoint, Barman WAL/PITR) the
# operand image must carry the timescaledb shared library at the *exact* live
# extension version so a pg_dump/restore lines the hypertable catalog up with no
# `ALTER EXTENSION timescaledb UPDATE` surprise.
#
# Patroni-based timescale/timescaledb-ha is NOT usable here: CNPG owns the
# instance lifecycle (its own entrypoint, instance manager, and PGDATA contract)
# and a Patroni image fights that. The supported pattern is: take the CNPG
# operand image as the base and add ONLY the timescaledb library + control
# files, preserving the CNPG entrypoint, user (26/postgres), and layout.
#
# PINNING
# -------
#   * Base: ghcr.io/cloudnative-pg/postgresql:16.13-standard-bookworm
#     (Debian bookworm; matches the family CNPG runs for the other clusters in
#     this cluster — auth-pg is on :16.13). Live prod runs PG 16.11; restoring a
#     16.11 dump into a 16.13 server is a forward minor upgrade and is safe
#     (pg_dump logical restore is minor-version forward compatible). CNPG keeps
#     the minor current thereafter.
#   * timescaledb: pinned to 2.25.2 (apt: timescaledb-2-postgresql-16=2.25.2*),
#     the EXACT live extension version. The community (TSL-licensed) package is
#     required because prod uses native compression (a TSL feature); the -oss
#     (Apache) build would refuse to load the compressed chunks.
#
# This image is NOT the prod-cutover image by itself — #245 governs that. It is
# the disposable database qualification operand for #396/#243/#244. It is
# not a second product environment; no production connection is changed.
ARG CNPG_PG_BASE=ghcr.io/cloudnative-pg/postgresql:16.13-standard-bookworm@sha256:99b3c3731cc560c1d91a98c8004230d62c6c5cf403d92e5ba5f75c11da896700
FROM ${CNPG_PG_BASE}

# timescaledb 2.25.2 for PG16. Pinned exactly to the live source-of-record.
#   TIMESCALEDB_VERSION  = the extension version that lands in PostgreSQL
#                          (timescaledb.control default_version, the catalog key)
#   TIMESCALEDB_APT_PKG  = the full Debian package version (upstream~distro-build)
# Keep both in lockstep when bumping.
ARG TIMESCALEDB_VERSION=2.25.2
ARG TIMESCALEDB_APT_PKG=2.25.2~debian12-1613
# Pin the base distro codename explicitly: the CNPG operand image has no
# lsb_release, and the apt repo path is per-codename. 16.x CNPG images are
# bookworm; the FROM line above asserts -bookworm.
ARG DEBIAN_CODENAME=bookworm

USER root

# Add the official Timescale apt repository (community/TSL build) and install
# ONLY the timescaledb extension + loader packages for PG16 at the pinned
# version. We do NOT run timescaledb-tune (CNPG manages postgresql.conf; tuning
# is supplied via the Cluster spec's postgresql.parameters, see the cluster
# manifest). The exact package version is asserted; the build FAILS LOUDLY if
# the pinned 2.25.2 build is ever pulled from the repo.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends gnupg wget ca-certificates; \
    echo "deb https://packagecloud.io/timescale/timescaledb/debian/ ${DEBIAN_CODENAME} main" \
        > /etc/apt/sources.list.d/timescaledb.list; \
    wget --quiet -O - https://packagecloud.io/timescale/timescaledb/gpgkey | gpg --dearmor \
        > /etc/apt/trusted.gpg.d/timescaledb.gpg; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        "timescaledb-2-postgresql-16=${TIMESCALEDB_APT_PKG}" \
        "timescaledb-2-loader-postgresql-16=${TIMESCALEDB_APT_PKG}"; \
    # Prove the pinned extension control file landed at the exact version.
    test -f "/usr/share/postgresql/16/extension/timescaledb--${TIMESCALEDB_VERSION}.sql"; \
    grep -q "default_version = '${TIMESCALEDB_VERSION}'" /usr/share/postgresql/16/extension/timescaledb.control; \
    apt-get purge -y --auto-remove gnupg wget; \
    rm -rf /var/lib/apt/lists/*

# The role-complete backup also requires vector 0.8.1. Do not inherit an
# unspecified extension version from the base image. Verify upstream source
# bytes before compiling against this exact PG16 operand.
ARG PGVECTOR_VERSION=0.8.1
ARG PGVECTOR_SHA256=a9094dfb85ccdde3cbb295f1086d4c71a20db1d26bf1d6c39f07a7d164033eb4
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends build-essential postgresql-server-dev-16 curl ca-certificates; \
    curl -fsSL "https://github.com/pgvector/pgvector/archive/refs/tags/v${PGVECTOR_VERSION}.tar.gz" -o /tmp/pgvector.tar.gz; \
    echo "${PGVECTOR_SHA256}  /tmp/pgvector.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/pgvector.tar.gz -C /tmp; \
    make -C "/tmp/pgvector-${PGVECTOR_VERSION}" OPTFLAGS=""; \
    make -C "/tmp/pgvector-${PGVECTOR_VERSION}" install; \
    grep -q "default_version = '${PGVECTOR_VERSION}'" /usr/share/postgresql/16/extension/vector.control; \
    rm -rf /tmp/pgvector* /var/lib/apt/lists/*

# barman-cloud CLI for in-tree barmanObjectStore WAL archiving + base backups +
# PITR. The CNPG `-standard-bookworm` operand base does NOT bundle barman-cloud
# (the newer images dropped it in favour of the Barman Cloud Plugin), so the
# in-tree backup path fails with "barman-cloud-check-wal-archive: not found"
# unless we add it here. Installed via apt (python3-barman / barman-cli-cloud +
# the cloud provider client). Pinned-loose (distro package) since this is the
# backup tooling, not the data engine; PROD can pin tighter if required.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends barman-cli-cloud python3-boto3; \
    barman-cloud-wal-archive --help >/dev/null; \
    barman-cloud-backup --help >/dev/null; \
    barman-cloud-check-wal-archive --help >/dev/null; \
    rm -rf /var/lib/apt/lists/*

# Restore the CNPG operand contract: the operator runs the instance as uid 26
# (postgres) and supplies its own entrypoint. We only added files as root; hand
# the runtime back to the CNPG postgres user.
USER 26

ARG GIT_SHA=unknown

LABEL org.opencontainers.image.source="https://github.com/VerdifyConsultancy/verdify-platform" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      io.verdify.pgvector.version="${PGVECTOR_VERSION}" \
      org.opencontainers.image.title="verdify-timescaledb-cnpg" \
      org.opencontainers.image.description="CloudNativePG operand image with TimescaleDB ${TIMESCALEDB_VERSION} for PG16 (Verdify HA-4, issue #243)" \
      io.verdify.timescaledb.version="${TIMESCALEDB_VERSION}" \
      io.verdify.cnpg.base="${CNPG_PG_BASE}"
