FROM quay.io/jupyter/base-notebook:latest

# Install common kernel-side dependencies used by the quickstarts and tools.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
