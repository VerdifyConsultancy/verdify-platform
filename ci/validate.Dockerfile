# ============================================================================
# verdify-ci-validate: the in-cluster validate step's image, with PostgreSQL 16
# preinstalled.
#
# Pinned by jvallery/agents, not here: WorkflowTemplate
# agent-fleet-ci/verdify-platform-ci, template `validate`, runs
# scripts/ci-local.sh in this image once the coordinator pins its digest there.
# Why: postgres_bin() in scripts/qualify-component-containment.py uses an
# installed /usr/lib/postgresql/16 toolchain when one is present. Otherwise it
# downloads and unpacks the PGDG packages on every validate run, which takes
# about 170 s per PR.
#
# Base: the exact python:3.13-bookworm index the validate template ran before
# this image (Debian 12 bookworm; it already ships g++, make, git, curl and
# tar). Change it only together with that template;
# tests/test_ci_validate_dockerfile.py keeps the two in step.
#
# Build: this image is not in .github/workflows/build.yml, so a merge does not build
# it. Submit a repo-build-verdifyconsultancy Workflow (dockerfile
# ci/validate.Dockerfile, image verdifyconsultancy/verdify-ci-validate); see
# docs/runbooks/prod-promotion.md. Kaniko and BuildKit read
# ci/validate.Dockerfile.dockerignore in place of the root .dockerignore, so
# the build context holds only the two COPY inputs below.
#
# Keep COPY lines plain: no ADD, no flag other than --from=<earlier stage>, and
# no $ in a COPY line. The in-cluster Kaniko path rebuilds every image on those.
# ============================================================================

# Runtime provenance, as in api/Dockerfile: repo-build passes the exact 40-hex
# revision as GIT_SHA. A build without it falls back to the detached
# checkout's .git/HEAD (from a linked worktree, pass GIT_SHA).
FROM mirror.gcr.io/library/python@sha256:353cf2106d143e1d28f5d7c10c5f5c0387085bba22ef0f7f7e52c2c330fb1779 AS source-metadata
ARG GIT_SHA=unknown
COPY .git /tmp/verdify-git-metadata
RUN set -eu; \
    candidate="$GIT_SHA"; \
    if ! printf '%s\n' "$candidate" | grep -Eq '^[0-9a-f]{40}$'; then \
        test -f /tmp/verdify-git-metadata/HEAD; \
        candidate="$(tr -d '\r\n' < /tmp/verdify-git-metadata/HEAD)"; \
    fi; \
    printf '%s\n' "$candidate" | grep -Eq '^[0-9a-f]{40}$'; \
    install -d -m 0755 /out; \
    printf '%s\n' "$candidate" > /out/source-revision; \
    chmod 0444 /out/source-revision

FROM mirror.gcr.io/library/python@sha256:353cf2106d143e1d28f5d7c10c5f5c0387085bba22ef0f7f7e52c2c330fb1779

# The PGDG signing key is the vendored copy that the qualifier also reads. The
# build checks it against the qualifier's pinned SHA-256 (PGDG_KEY_SHA256)
# before apt uses it, and signed-by limits its trust to the PGDG source.
COPY db/qualification/pgdg-ACCC4CF8.asc /etc/apt/keyrings/pgdg-ACCC4CF8.asc

# Install the PostgreSQL 16 server and client at the paths postgres_bin()
# checks. These are packages only: with create_main_cluster = false the package
# scripts create no cluster, so no server can start. The containment fixture
# initialises its own private cluster at test time.
RUN set -eux; \
    export DEBIAN_FRONTEND=noninteractive; \
    echo '0144068502a1eddd2a0280ede10ef607d1ec592ce819940991203941564e8e76  /etc/apt/keyrings/pgdg-ACCC4CF8.asc' | sha256sum -c -; \
    chmod 0644 /etc/apt/keyrings/pgdg-ACCC4CF8.asc; \
    grep -qx 'VERSION_CODENAME=bookworm' /etc/os-release; \
    echo 'deb [signed-by=/etc/apt/keyrings/pgdg-ACCC4CF8.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main' > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends postgresql-common; \
    sed -ri 's/#(create_main_cluster) .*$/\1 = false/' /etc/postgresql-common/createcluster.conf; \
    grep -qx 'create_main_cluster = false' /etc/postgresql-common/createcluster.conf; \
    apt-get install -y --no-install-recommends postgresql-16 postgresql-client-16; \
    rm -rf /var/lib/apt/lists/*; \
    test -x /usr/lib/postgresql/16/bin/initdb; \
    test -f /usr/share/postgresql/16/extension/pgcrypto.control; \
    /usr/lib/postgresql/16/bin/postgres --version; \
    test -z "$(pg_lsclusters --no-header)"

# kustomize v5.4.3 is the release the validate template downloads, without a
# checksum, on every run (ci-local.sh renders the prod overlay with it). This
# copy is checked against the release's published SHA-256. The template's
# download step can be removed once this image is pinned.
RUN set -eux; \
    curl -fsSL --retry 3 -o /tmp/kustomize.tar.gz 'https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv5.4.3/kustomize_v5.4.3_linux_amd64.tar.gz'; \
    echo '3669470b454d865c8184d6bce78df05e977c9aea31c30df3c669317d43bcc7a7  /tmp/kustomize.tar.gz' | sha256sum -c -; \
    tar -xzf /tmp/kustomize.tar.gz --no-same-owner -C /usr/local/bin kustomize; \
    rm /tmp/kustomize.tar.gz; \
    kustomize version

COPY --from=source-metadata /out/source-revision /etc/verdify/source-revision

# The managed Kaniko invocation sets the revision and created OCI labels. There
# is no ENV, USER or WORKDIR: the validate step runs as root in the base's
# environment.
LABEL org.opencontainers.image.source="https://github.com/VerdifyConsultancy/verdify-platform" \
      org.opencontainers.image.title="verdify-ci-validate"
