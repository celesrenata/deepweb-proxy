# DeepWeb Proxy System

A robust system for crawling, analyzing, and reporting on deep web content with content moderation capabilities.

## Overview

This project provides a comprehensive solution for accessing, storing, and analyzing content from various web sources, including deep web .onion and .i2p sites. It includes:

- Web crawling and content extraction
- MySQL database storage for sites, pages, and media
- AI-powered content analysis and moderation
- HTML report generation for analysis results
- Illicit content detection with AI-assisted moderation

## Prerequisites

- Docker and Docker Compose (for containerized deployment)
- Kubernetes cluster (for K8s deployment)
- Ollama service (external requirement)
- MySQL database (included in Docker setup, configured in K8s)

## Deployment Options

### 1. Docker Deployment

#### Quick Start with Docker Compose

```shell script
# Clone the repository
git clone https://github.com/celesrenata/deepweb-proxy.git
cd deepweb-proxy

# Create .env file with your configuration
cp .env.example .env
# Edit .env file with your settings

# Start the services
docker-compose up -d
```


#### Docker Compose Configuration

Create a `docker-compose.yml` file in the project root:

```yaml
version: '3.8'

services:
  app:
    build:
      context: .
      dockerfile: docker/Dockerfile
    restart: unless-stopped
    volumes:
      - ./output:/app/output
    environment:
      - MYSQL_HOST=mysql
      - MYSQL_PORT=3306
      - MYSQL_USER=splinter-research
      - MYSQL_PASSWORD=PSCh4ng3me!
      - MYSQL_DATABASE=splinter-research
      - OLLAMA_ENDPOINT=http://your-ollama-host:2701/api/generate
      - AI_MODEL=llama3.1:8b
      - OUTPUT_DIR=/app/output
    depends_on:
      - mysql

  mysql:
    image: mysql:8.0
    restart: unless-stopped
    environment:
      - MYSQL_ROOT_PASSWORD=strongRootPassword
      - MYSQL_DATABASE=splinter-research
      - MYSQL_USER=splinter-research
      - MYSQL_PASSWORD=PSCh4ng3me!
    volumes:
      - mysql_data:/var/lib/mysql
    ports:
      - "3306:3306"
    command: --default-authentication-plugin=mysql_native_password

  webserver:
    build:
      context: .
      dockerfile: docker/Dockerfile
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      - MYSQL_HOST=mysql
      - MYSQL_PORT=3306
      - MYSQL_USER=splinter-research
      - MYSQL_PASSWORD=PSCh4ng3me!
      - MYSQL_DATABASE=splinter-research
    command: ["python", "webserver.py"]
    depends_on:
      - mysql

volumes:
  mysql_data:
```


### 2. Kubernetes Deployment

#### Prerequisites for K8s

- Kubernetes cluster with kubectl configured
- External Ollama service (accessible via network)
- Storage provisioner for PersistentVolumes (optional: NFS setup included)

#### Deploying to Kubernetes

1. Configure the MySQL secret:

```shell script
kubectl apply -f mysql-secrets.yaml
```


2. Create a persistent volume for MySQL (optional if using cloud storage):

```shell script
kubectl apply -f nfs-pv.yaml
kubectl apply -f nfs-pvc.yaml
```


3. Deploy the application:

```shell script
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
```


#### Kubernetes Configuration Files

The repository includes necessary K8s configuration files:

- `deployment.yaml`: Main application deployment
- `service.yaml`: Service configuration for accessing the application
- `mysql-secrets.yaml`: Database credentials
- `nfs-pv.yaml` & `nfs-pvc.yaml`: Storage configuration

## Configuring Ollama

This application relies on an external Ollama service for AI analysis. Configure your connection to Ollama by setting the environment variable:

```
OLLAMA_ENDPOINT=http://your-ollama-host:2701/api/generate
AI_MODEL=llama3.1:8b  # or your preferred model
```


For Docker, set these in your `.env` file or `docker-compose.yml`.
For Kubernetes, set them in your `deployment.yaml` file.

## Features

- **Web Crawling**: Automated crawling of standard and deep web sites
- **Content Storage**: Structured database for storing crawled content
- **Media Analysis**: AI-powered analysis of images and other media
- **Content Moderation**: Detection of potentially illicit content
- **HTML Reporting**: Generate interactive HTML reports of analysis results

## Usage

### Running Content Analysis

```shell script
# Inside the container
python ai_analysis.py

# Or via Docker
docker exec -it deepweb-proxy-app python ai_analysis.py
```


### Running Illicit Content Detection

```shell script
# Inside the container
python illicit_content_detector.py --threshold 30

# Or via Docker
docker exec -it deepweb-proxy-app python illicit_content_detector.py --threshold 30
```


### Accessing the Web Interface

Once deployed, access the web interface at:
- Docker: http://localhost:8080
- Kubernetes: Use the Service IP or configured Ingress

## Security Considerations

This application is designed to handle potentially sensitive content:

- Ensure proper access controls on all deployment platforms
- Regularly rotate MySQL credentials
- Restrict network access to the Ollama service
- Review the generated reports regularly for moderation

## License

This project is proprietary and confidential.

## Support

For support and questions, please contact the development team.
