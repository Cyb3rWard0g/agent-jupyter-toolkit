FROM quay.io/jupyter/base-notebook:2026-08-24@sha256:23fbe16371af3c0fdc833fa0962b42fd07811e152b821ff51d19a8d6e9cc86cf

# Install kernel-side dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && python -m pip freeze --all > /opt/conda/jupyter-package-inventory.txt
