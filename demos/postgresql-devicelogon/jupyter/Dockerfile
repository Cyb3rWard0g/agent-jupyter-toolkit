FROM quay.io/jupyter/base-notebook:latest

# Install kernel-side dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
