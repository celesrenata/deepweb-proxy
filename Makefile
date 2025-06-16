# Makefile for deepweb-proxy Nix-based project

# Variables
PYTHON = python3
PIP = pip3
NIX_BUILD = nix-build
NIX_SHELL = nix-shell
DOCKER_LOAD = docker load
KUBECTL = kubectl
DOCKER_IMAGE = ghcr.io/celesrenata/deepweb-proxy:latest

# Python dependencies (for local development)
.PHONY: install
install:
	$(PIP) install -r requirements.txt
	$(PIP) install fastapi uvicorn

# Start services individually (for local development)
.PHONY: run-webserver
run-webserver:
	$(PYTHON) webserver.py

.PHONY: run-engine
run-engine:
	$(PYTHON) mcp_engine.py

# Build Docker image using Nix
.PHONY: nix-build
nix-build:
	$(NIX_BUILD) -A dockerImage

# Load the built image into Docker
.PHONY: docker-load
docker-load: nix-build
	$(DOCKER_LOAD) < result

.PHONY: debug-image
debug-image: docker-load
	docker create --name debug-container deepweb-proxy:latest
	docker export debug-container | tar -tvf - | grep app/
	docker rm debug-container

# Use the NixOS development environment
.PHONY: dev-env
dev-env:
	$(NIX_SHELL) shell.nix

# Build and load in one step
.PHONY: build
build: nix-build docker-load

# Enter nix-shell for development
.PHONY: shell
shell:
	$(NIX_SHELL)

# Run the application in Docker
.PHONY: docker-run
docker-run:
	docker run -p 8080:8080 -d --name deepweb-proxy deepweb-proxy:latest

.PHONY: docker-stop
docker-stop:
	docker stop deepweb-proxy || true
	docker rm deepweb-proxy || true

# Kubernetes deployment
.PHONY: k8s-deploy
k8s-deploy:
	$(KUBECTL) apply -f deployment.yaml

.PHONY: k8s-delete
k8s-delete:
	$(KUBECTL) delete -f deployment.yaml

# GitHub Container Registry publishing
.PHONY: docker-tag
docker-tag: docker-load
	docker tag deepweb-proxy:latest $(DOCKER_IMAGE)

.PHONY: docker-push
docker-push: docker-tag
	docker push $(DOCKER_IMAGE)

# Development helpers
.PHONY: clean
clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name "*.pyd" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -f result

.PHONY: all
all: build

.DEFAULT_GOAL := all
