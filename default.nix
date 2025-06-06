{ pkgs ? import <nixpkgs> {} }:

let
  # Create a Python environment with required packages
  pythonEnv = pkgs.python3.withPackages (ps: with ps; [
    requests beautifulsoup4 fastapi uvicorn
    pymysql sqlalchemy cryptography
    python-dateutil aiohttp jinja2
    pydantic urllib3
    pillow # For media file handling and image processing
    pysocks # For SOCKS proxy support (Tor)
  ]);

  # Define the image
  image = pkgs.dockerTools.buildLayeredImage {
    name = "deepweb-proxy";
    tag = "latest";

    # Configure container settings
    config = {
      Cmd = [ "${pkgs.bash}/bin/bash" "/app/entrypoint.sh" ];
      ExposedPorts = {
        "8080/tcp" = {};
        "9050/tcp" = {};
        "4444/tcp" = {};
        "7070/tcp" = {};  # Add web console port
      };
      WorkingDir = "/app";
      # Add environment variables for MySQL connection
      Env = [
        "MYSQL_HOST=10.1.1.12"
        "MYSQL_PORT=3306"
        "MYSQL_USER=splinter-research"
        "MYSQL_PASSWORD=PASSWORD_HERE" # Replace with actual password in production
        "MYSQL_DATABASE=splinter-research"
        "MAX_MEDIA_SIZE=10000000" # 10MB limit for media files
        "MAX_MEDIA_PER_PAGE=20" # Maximum media files per page
        "CRAWL_DEPTH=1"
        "MAX_PAGES_PER_SITE=100"
      ];
    };

    # Use contents for simplicity
    contents = [
      pythonEnv
      pkgs.bash
      pkgs.coreutils
      pkgs.procps
      pkgs.iproute2
      pkgs.gnugrep
      pkgs.tor
      pkgs.i2pd
      pkgs.curl  # Add curl for testing
      pkgs.file  # For mime type detection
      pkgs.cacert # For SSL certificate validation
      pkgs.socat  # For debugging network connections
    ];

    # Create the application files directly in the image
    extraCommands = ''
      mkdir -p app
      mkdir -p var/lib/tor
      mkdir -p var/lib/i2pd
      mkdir -p run
      mkdir -p tmp
      mkdir -p etc/i2pd
      mkdir -p mnt/config
      chmod 1777 tmp

      cp ${./entrypoint.sh} app/entrypoint.sh
      cp ${./mcp_engine.py} app/mcp_engine.py
      cp ${./webserver.py} app/webserver.py
      cp ${./db_models.py} app/db_models.py
      chmod +x app/entrypoint.sh

      # Create default sites.txt file
      mkdir -p mnt/config
      cat > mnt/config/sites.txt << EOF
https://news.ycombinator.com/
EOF

      # Create I2P configuration file
      cat > etc/i2pd/i2pd.conf << EOF
# Main configuration
ipv4 = true
ipv6 = false
notransit = true
floodfill = false
nat = true

# Use faster tunnel settings for better performance in containers
inbound.length = 1
outbound.length = 1
inbound.quantity = 2
outbound.quantity = 2

# HTTP Proxy configuration
httpproxy.enabled = true
httpproxy.address = 0.0.0.0
httpproxy.port = 4444
httpproxy.addresshelper = true
httpproxy.outproxy.enabled = false

# Web console for debugging
http.enabled = true
http.address = 0.0.0.0
http.port = 7070

# Logging
log = stdout
loglevel = info

# Set a longer connection timeout
ntcp2.timeout = 60000

# Increase bootstrap time
reseed.verify = false
reseed.urls = https://reseed.i2p-projekt.de/,https://i2p.mooo.com/netDb/,https://reseed.i2p2.no/
EOF
    '';
  };
in
{
  # Expose the image with the attribute name "dockerImage" to match the Makefile
  dockerImage = image;
}