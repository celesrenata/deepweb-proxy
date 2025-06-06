{ config, pkgs, ... }:
{
  # Enable TOR and I2P services
  services.tor.enable = true;
  services.i2pd.enable = true;

  # Install Python and dependencies for the MCP
  environment.systemPackages = with pkgs; [
    python3
    python3Packages.pydantic
    python3Packages.sqlalchemy
    python3Packages.cryptography
    python3Packages.alembic
    python3Packages.aiohttp
    python3Packages.jinja2
    python3Packages.requests
    python3Packages.beautifulsoup4
    python3Packages.fastapi
    python3Packages.uvicorn
    python3Packages.pip
    python3Packages.pillow
    python3Packages.python-dateutil
    python3Packages.urllib3
    python3Packages.pysocks  # For SOCKS proxy support
    file          # For mime type detection
    tor
    i2pd
    cacert        # SSL certificates
    mariadb       # Database client for direct access
    socat         # For debugging network connections
  ];

  # Configure larger file upload size for MySQL
  services.mysql = {
    enable = true;
    package = pkgs.mariadb;
    settings = {
      mysqld = {
        max_allowed_packet = "16M";
        innodb_log_file_size = "64M";
        # Increase buffer sizes for better performance with large content
        innodb_buffer_pool_size = "256M";
        # Increase timeouts for long-running queries
        connect_timeout = 60;
        wait_timeout = 600;
      };
    };
  };

  # Add additional NixOS settings here if needed
}