SHELL=/bin/bash

.DEFAULT_GOAL := default

.PHONY: clean build

VERSION = 0.1

default: all ## default target is all

help: ## display this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

all: clean build ## clean and build

install: ## install the package
	pip install .

dev: ## install for development
	pip install -e ".[dev]"

test: ## run the unit tests
	pytest

build: ## build the package
	pip install build
	python -m build .

clean: ## clean build artifacts
	rm -rf dist/ build/ *.egg-info src/*.egg-info

build-docker: ## build the docker image
	docker buildx build --platform linux/amd64,linux/arm64 --push -t cyb3rward0g/mcp-jupyter-notebook:${VERSION} .
	docker buildx build --platform linux/amd64,linux/arm64 --push -t cyb3rward0g/mcp-jupyter-notebook:latest .
	@exec echo open https://hub.docker.com/r/cyb3rward0g/mcp-jupyter-notebook/tags

start-docker: ## start the mcp server in docker
	docker run -i --rm \
	  -e JUPYTER_BASE_URL=http://localhost:8888 \
	  -e JUPYTER_TOKEN=MY_TOKEN \
	  --network=host \
	  cyb3rward0g/mcp-jupyter-notebook:latest

pull-docker: ## pull the latest docker image
	docker image pull cyb3rward0g/mcp-jupyter-notebook:latest

push-docker: ## push the docker image to the registry
	docker push cyb3rward0g/mcp-jupyter-notebook:${VERSION}
	docker push cyb3rward0g/mcp-jupyter-notebook:latest
	@exec echo open https://hub.docker.com/r/cyb3rward0g/mcp-jupyter-notebook/tags

publish-pypi: ## publish the pypi package
	rm -rf dist/ && python -m build
	@exec echo
	@exec echo twine upload ./dist/*-py3-none-any.whl
	@exec echo
	@exec echo https://pypi.org/project/mcp-jupyter-notebook/#history
