{ config, pkgs, ... }:
{
  imports = [
    ./deepweb-proxy.nix
  ];

  # Container-specific file descriptor limits
  security.pam.loginLimits = [
    {
      domain = "*";
      type = "soft";
      item = "nofile";
      value = "65536";
    }
    {
      domain = "*";
      type = "hard";
      item = "nofile";
      value = "65536";
    }
    {
      domain = "root";
      type = "soft";
      item = "nofile";
      value = "65536";
    }
    {
      domain = "root";
      type = "hard";
      item = "nofile";
      value = "65536";
    }
  ];

  # Kernel parameters optimized for container environment
  boot.kernel.sysctl = {
    "fs.file-max" = 1048576;
    "fs.nr_open" = 1048576;
    "net.core.somaxconn" = 32768;
    "net.core.netdev_max_backlog" = 2048;
    "net.ipv4.tcp_max_syn_backlog" = 4096;
    "vm.max_map_count" = 262144;
  };

  # Systemd configuration for container
  systemd.extraConfig = ''
    DefaultLimitNOFILE=65536
    DefaultLimitNPROC=4096
  '';

  # Enable TOR and I2P services with container-optimized settings
  services.tor = {
    enable = true;
    settings = {
      SocksPort = [{
        addr = "0.0.0.0";
        port = 9050;
      }];
      ControlPort = 9051;
      DataDirectory = "/var/lib/tor";
      ExitRelay = false;
      ClientOnly = true;
      # Conservative resource limits for container
      MaxMemInQueues = "256 MB";
      ConstrainedSockets = true;
      ConstrainedSockSize = 4096;
      ConnectionPadding = false;
      ReducedConnectionPadding = true;
    };
  };

  services.i2pd = {
    enable = true;
    bandwidth = 128;  # Very conservative for container
    share = 5;
    ntcp = false;
    ntcp2.enable = true;
    ssu2.enable = false;  # Disable to reduce file descriptor usage
    floodfill = false;
    notransit = true;

    # Very conservative limits for container
    limits = {
      transittunnels = 5;
      openfiles = 4096;  # Much lower for container
      coresize = 0;
    };

    # HTTP proxy settings
    proto.http = {
      enable = true;
      address = "0.0.0.0";
      port = 4444;
    };

    # Web console
    proto.httpProxy = {
      enable = true;
      address = "0.0.0.0";
      port = 7070;
    };

    # Additional container-specific I2P settings
    extraConfig = ''
      # Disable reseeding to reduce file descriptor usage
      reseed.verify = false
      reseed.floodfill = false
      reseed.threshold = 0

      # Minimal bootstrap settings
      exploratory.inbound.length = 1
      exploratory.outbound.length = 1
      exploratory.inbound.quantity = 1
      exploratory.outbound.quantity = 1

      # Disable unused services
      upnp.enabled = false
      addressbook.subscriptions =
    '';
  };

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
    python3Packages.minio
    python3Packages.pip
    python3Packages.pillow
    python3Packages.python-dateutil
    python3Packages.urllib3
    python3Packages.pysocks  # For SOCKS proxy support
    file          # For mime type detection
    tor
    i2pd
    cacert        # SSL certificates
    openssl       # SSL certificates
    mariadb       # Database client for direct access
    net-tools
    socat         # For debugging network connections
    procps        # For process monitoring
    util-linux    # For system utilities
  ];

  # Configure MySQL with container-appropriate settings
  services.mysql = {
    enable = true;
    package = pkgs.mariadb;
    settings = {
      mysqld = {
        max_allowed_packet = "16M";
        innodb_log_file_size = "64M";
        # Container-appropriate buffer sizes
        innodb_buffer_pool_size = "128M";  # Reduced for container
        # Timeouts
        connect_timeout = 60;
        wait_timeout = 600;
        # File descriptor limits
        open_files_limit = 1024;  # Conservative for container
        table_open_cache = 256;
      };
    };
  };

  # Container-specific systemd service overrides
  systemd.services.tor.serviceConfig = {
    LimitNOFILE = 4096;
    LimitNPROC = 1024;
  };

  systemd.services.i2pd.serviceConfig = {
    LimitNOFILE = 1024;  # Very conservative
    LimitNPROC = 512;
  };

  systemd.services.mysql.serviceConfig = {
    LimitNOFILE = 2048;
    LimitNPROC = 1024;
  };

  # Add additional NixOS settings here if needed
}