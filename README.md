# DeepWeb Proxy System
A robust system for crawling, analyzing, and reporting on deep web content with advanced AI-powered content moderation capabilities.
## Overview
This project provides a comprehensive solution for accessing, storing, and analyzing content from various web sources, including deep web .onion and .i2p sites. It includes:
- Web crawling and content extraction (including Tor and I2P networks)
- MySQL database storage for sites, pages, and media
- Multi-model AI analysis of text and images
- Content moderation with illicit content detection
- HTML report generation for analysis results

## Prerequisites
- Docker and Docker Compose (for containerized deployment)
- Kubernetes cluster (for K8s deployment)
- Ollama service with required AI models (external requirement)
- MySQL database (included in Docker setup, configured in K8s)

## AI Models
The system uses multiple AI models for different tasks:
1. **llava:13b** - Multimodal model for image description generation
2. - Text model for content analysis and understanding **gemma3:12b**
3. - Efficient model for content moderation and illicit content detection **llama3.1:8b**

All models are served via an external Ollama instance that must be configured separately.
## Deployment Options
### 1. Docker Deployment
#### Quick Start with Docker Compose
``` bash
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
The project includes a file with: `docker-compose.yaml`
- Application container (deepweb-proxy)
- MySQL database
- Web interface container
``` yaml
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
``` bash
kubectl apply -f mysql-secrets.yaml
```
1. Create a persistent volume for MySQL (optional if using cloud storage):
``` bash
kubectl apply -f nfs-pv.yaml
kubectl apply -f nfs-pvc.yaml
```
1. Deploy the application:
``` bash
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
```
#### Kubernetes Configuration Files
The repository includes necessary K8s configuration files:
- : Main application deployment `deployment.yaml`
- : Service configuration for accessing the application `service.yaml`
- : Database credentials `mysql-secrets.yaml`
- & : Storage configuration `nfs-pv.yaml``nfs-pvc.yaml`

## Configuring Ollama
This application relies on an external Ollama service for AI analysis with multiple models. Configure your connection to Ollama by setting the environment variable:
``` 
OLLAMA_ENDPOINT=http://your-ollama-host:2701/api/generate
```
Make sure your Ollama instance has the following models installed:
- `llava:13b` - For image description generation
- - For text content analysis `gemma3:12b`
- - For content moderation `llama3.1:8b`

For Docker, set these in your file or `docker-compose.yml`. For Kubernetes, set them in your file. `.env``deployment.yaml`
## Features
- **Multi-Network Crawling**: Support for standard web, Tor (.onion), and I2P networks
- **Content Storage**: Structured database for storing crawled pages and media
- **Media Analysis**: AI-powered visual analysis of images
- **Content Moderation**: Detection of potentially illicit content with multi-stage filtering
- **HTML Reporting**: Generate interactive HTML reports of analysis results

## Usage
### Running the Web Crawler
``` bash
# Inside the container
python mcp_engine.py

# Or via Docker
docker exec -it deepweb-proxy-app python mcp_engine.py
```
### Running Image Description Analysis
``` bash
# Inside the container
python image_description_analyzer.py

# Or via Docker
docker exec -it deepweb-proxy-app python image_description_analyzer.py
```
### Running Illicit Content Detection
``` bash
# Inside the container
python illicit_content_detector.py --threshold 30

# Or via Docker
docker exec -it deepweb-proxy-app python illicit_content_detector.py --threshold 30
```
### Accessing the Web Interface
Once deployed, access the web interface at:
- Docker: [http://localhost:8080](http://localhost:8080)
- Kubernetes: Use the Service IP or configured Ingress

## Security Considerations
This application is designed to handle potentially sensitive content:
- Ensure proper access controls on all deployment platforms
- Regularly rotate MySQL credentials
- Restrict network access to the Ollama service
- Review the generated reports regularly for moderation
- Use a secure and isolated environment for the Ollama service

## License
This project is proprietary and confidential.
## Support
For support and questions, please contact the development team.

