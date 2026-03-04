FROM python:3.12-slim

# Version injected at build time so hatch-vcs works without .git history.
# Usage: docker build --build-arg VERSION=0.3.0 .
ARG VERSION
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION}

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src/ src/

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m pip install --upgrade pip wheel setuptools && pip --version

RUN pip install --no-cache-dir .

EXPOSE 4040

ENTRYPOINT ["python", "-m", "mcp_jupyter_notebook"]
