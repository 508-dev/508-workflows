# Isolated OMP authoring sidecar. The OMP release is older than the project's
# seven-day dependency cooldown and each architecture binary is verified before
# it is installed. Do not replace this with a curl-to-shell installer.
FROM python@sha256:93ab4b7fa528b25124c97bcc755415e60eb671a86b4dbe0328df2fe2d1c1193d

ARG TARGETARCH
ARG OMP_VERSION=18.0.3
ARG OMP_LINUX_X64_SHA256=4733662062bfb4364e4ab387d7940e56a7143e5dc1c20726787e61bb1b52df28
ARG OMP_LINUX_ARM64_SHA256=3b7e6ec68175451ec0ae2e07aac042d180d6ee03dbea11851aa84c050b7995f6

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) omp_asset=omp-linux-x64; omp_sha256="$OMP_LINUX_X64_SHA256" ;; \
      arm64) omp_asset=omp-linux-arm64; omp_sha256="$OMP_LINUX_ARM64_SHA256" ;; \
      *) echo "Unsupported OMP sandbox architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl --fail --location --silent --show-error \
      "https://github.com/can1357/oh-my-pi/releases/download/v${OMP_VERSION}/${omp_asset}" \
      --output /tmp/omp; \
    echo "${omp_sha256}  /tmp/omp" | sha256sum --check --status; \
    install --mode=0555 /tmp/omp /usr/local/bin/omp; \
    rm -f /tmp/omp

WORKDIR /app
COPY packages/shared/src/five08/wiki_editing/omp_sandbox_server.py /app/omp_sandbox_server.py
COPY packages/shared/src/five08/wiki_editing/omp_egress_proxy.py /app/omp_egress_proxy.py

ENV PYTHONDONTWRITEBYTECODE=1
USER 65532:65532
ENTRYPOINT ["python3", "/app/omp_sandbox_server.py"]
