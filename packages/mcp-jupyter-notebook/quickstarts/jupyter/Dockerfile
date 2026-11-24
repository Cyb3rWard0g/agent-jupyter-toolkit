FROM quay.io/jupyter/base-notebook:2026-08-24@sha256:23fbe16371af3c0fdc833fa0962b42fd07811e152b821ff51d19a8d6e9cc86cf

ARG NOTEBOOK_REQUIREMENTS=packages/mcp-jupyter-notebook/quickstarts/jupyter/requirements.txt
COPY uv.lock /tmp/jupyter-build/uv.lock
COPY packages/mcp-jupyter-notebook/quickstarts/jupyter/locked_stack.py /tmp/jupyter-build/locked_stack.py
COPY ${NOTEBOOK_REQUIREMENTS} /tmp/jupyter-build/requirements.txt

RUN stack_file=$(mktemp) \
    && python /tmp/jupyter-build/locked_stack.py /tmp/jupyter-build/uv.lock > "$stack_file" \
    && python -m pip install --no-cache-dir \
        -r "$stack_file" -r /tmp/jupyter-build/requirements.txt \
    && python /tmp/jupyter-build/locked_stack.py /tmp/jupyter-build/uv.lock --check-installed \
    && python -m pip check \
    && python -m pip freeze --all > /opt/conda/jupyter-package-inventory.txt \
    && rm "$stack_file"
