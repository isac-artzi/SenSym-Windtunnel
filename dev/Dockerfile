# WindLab in Docker: the same OS and OpenFOAM version students get on WSL2 (Ubuntu 24.04 +
# OpenFOAM v2606), built for arm64 (Apple Silicon) and x86-64 alike. Used by dev/compose.yaml
# (scripts/run_docker.sh starts WindLab in it; see docs/INSTALL.md, "macOS" and "Other Linux").
# WindLab's own code is not copied in: the repository is mounted at /work when the container runs.
FROM ubuntu:24.04

ARG OPENFOAM_VERSION=2606
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    OPENFOAM_VERSION=${OPENFOAM_VERSION}

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git nano less htop time \
        python3 python3-pip python3-venv python3-dev build-essential \
        libgl1 libglu1-mesa libxrender1 nodejs \
    && curl -s https://dl.openfoam.com/add-debian-repo.sh | bash \
    && apt-get update && apt-get install -y --no-install-recommends \
        openfoam${OPENFOAM_VERSION}-default \
    && rm -rf /var/lib/apt/lists/* \
    # Security: forbid #codeStream/systemCall and other system operations in OpenFOAM dictionaries globally
    && sed -i 's/^\(\s*allowSystemOperations\s*\)1;/\10;/' \
        /usr/lib/openfoam/openfoam${OPENFOAM_VERSION}/etc/controlDict \
    && grep -q 'allowSystemOperations *0;' /usr/lib/openfoam/openfoam${OPENFOAM_VERSION}/etc/controlDict

# Python tooling for the WindLab backend and geometry work (the same packages pip install ".[dev,step]" gives)
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir \
        numpy scipy matplotlib numpy-stl trimesh \
        fastapi "uvicorn[standard]" pydantic \
        foamlib pytest ruff httpx python-multipart \
        cadquery-ocp
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/work
# `windlab` command runs the live source in /work (no reinstall needed after a git pull)
RUN printf '#!/bin/sh\nexec /opt/venv/bin/python -m windlab.cli "$@"\n' > /usr/local/bin/windlab \
    && chmod +x /usr/local/bin/windlab

# Non-root user, matching the student setup; /runs holds the simulation output (a Docker volume)
RUN useradd -m -s /bin/bash windlab \
    && echo "source /usr/lib/openfoam/openfoam${OPENFOAM_VERSION}/etc/bashrc" >> /home/windlab/.bashrc \
    && echo 'export PATH=/opt/venv/bin:$PATH' >> /home/windlab/.bashrc \
    && mkdir -p /runs && chown windlab:windlab /runs
USER windlab
# Optional, for maintainers only: an AI coding assistant inside the container (build with CLAUDE_CODE=1)
ARG CLAUDE_CODE=0
RUN if [ "$CLAUDE_CODE" = "1" ]; then \
        curl -fsSL https://claude.ai/install.sh | bash \
        && echo 'export PATH=$HOME/.local/bin:$PATH' >> /home/windlab/.bashrc \
        && mkdir -p /home/windlab/.claude; \
    fi
ENV PATH=/home/windlab/.local/bin:$PATH
WORKDIR /work
EXPOSE 8080
CMD ["bash", "-l"]
