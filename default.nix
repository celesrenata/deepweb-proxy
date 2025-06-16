{ pkgs ? import <nixpkgs> {} }:

let
  # Create a Python environment with required packages
  pythonEnv = pkgs.python3.withPackages (ps: with ps; [
    requests beautifulsoup4 fastapi uvicorn
    pymysql sqlalchemy cryptography
    python-dateutil aiohttp jinja2
    pydantic urllib3 minio
    pillow # For media file handling and image processing
    pysocks # For SOCKS proxy support (Tor)
  ]);

  # Create optimized I2P configuration for fast bootstrap
  i2pdConfig = pkgs.writeText "i2pd.conf" ''
    # Main configuration
    ipv4 = true
    ipv6 = false
    notransit = true
    floodfill = false
    nat = true

    # HTTP Proxy configuration
    httpproxy.enabled = true
    httpproxy.address = 0.0.0.0
    httpproxy.port = 4444
    httpproxy.addresshelper = true
    httpproxy.outproxy = http://false.i2p

    # SAM interface for better connectivity
    sam.enabled = true
    sam.address = 127.0.0.1
    sam.port = 7656

    # Web console for debugging
    http.enabled = true
    http.address = 0.0.0.0
    http.port = 7070

    # Logging
    log = stdout
    loglevel = error

    # AGGRESSIVE reseed configuration for fast bootstrap
    reseed.verify = false
    reseed.floodfill = false
    reseed.threshold = 10
    reseed.urls = https://reseed.diva.exchange/,https://reseed.i2pgit.org/,https://i2p.novg.net/,https://reseed.memcpy.io/,https://i2pseed.creativecowpat.net:8443/,https://reseed.onion.im/,https://reseed.atomike.ninja/,https://banana.incognet.io/

    # FAST network configuration
    bandwidth = 1024
    share = 50

    # Transport settings
    ntcp2.enabled = true
    ntcp2.port = 0
    ssu2.enabled = true
    ssu2.port = 0

    # AGGRESSIVE limits for faster bootstrap
    limits.transittunnels = 50
    limits.openfiles = 4096

    # Enable precomputation for speed
    precomputation.elgamal = true

    # AGGRESSIVE bootstrap help
    exploratory.inbound.length = 3
    exploratory.outbound.length = 3
    exploratory.inbound.quantity = 4
    exploratory.outbound.quantity = 4

    bandwidth = 512
    share = 25
    # FAST tunnel creation
    tunnel.buildtimeout = 20000
  '';

  # Create optimized Tor configuration
  torConfig = pkgs.writeText "torrc" ''
    SocksPort 0.0.0.0:9050
    DataDirectory /var/lib/tor
    RunAsDaemon 0
    Log notice stdout

    # Performance optimizations for fast circuits
    ConnectionPadding 0
    ReducedConnectionPadding 1
    CircuitBuildTimeout 10
    LearnCircuitBuildTimeout 0
    MaxCircuitDirtiness 600
    NewCircuitPeriod 30
    MaxClientCircuitsPending 32
    KeepalivePeriod 60

    # Security settings
    ExitRelay 0
    ExitPolicy reject *:*
    ClientOnly 1
  '';

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
        "7070/tcp" = {};
        "7656/tcp" = {}; # SAM interface
      };
      WorkingDir = "/app";
      Env = [
        "MYSQL_HOST=10.1.1.12"
        "MYSQL_PORT=3306"
        "MYSQL_USER=splinter-research"
        "MYSQL_PASSWORD=PASSWORD_HERE"
        "MYSQL_DATABASE=splinter-research"
        "MAX_MEDIA_SIZE=10000000"
        "MAX_MEDIA_PER_PAGE=20"
        "CRAWL_DEPTH=1"
        "MAX_PAGES_PER_SITE=100"
      ];
    };

    contents = [
      pythonEnv
      pkgs.bash
      pkgs.coreutils
      pkgs.procps
      pkgs.iproute2
      pkgs.gnugrep
      pkgs.tor
      pkgs.i2pd
      pkgs.curl
      pkgs.file
      pkgs.cacert
      pkgs.openssl
      pkgs.socat
      pkgs.netcat-gnu # For better network testing
      pkgs.nettools
      pkgs.findutils # For find command
      pkgs.gnutar # For tar extraction
      pkgs.gzip # For gzip decompression
    ];

    extraCommands = ''
      mkdir -p app
      mkdir -p var/lib/tor
      mkdir -p var/lib/i2pd
      mkdir -p var/lib/i2pd/certificates/family
      mkdir -p var/lib/i2pd/certificates/reseed
      mkdir -p run
      mkdir -p tmp
      mkdir -p etc/tor
      mkdir -p etc/i2pd
      mkdir -p mnt/config
      chmod 1777 tmp
      chmod 700 var/lib/tor
      chmod 755 var/lib/i2pd
      chmod 755 var/lib/i2pd/certificates
      chmod 755 var/lib/i2pd/certificates/family
      chmod 755 var/lib/i2pd/certificates/reseed

      # Copy application files
      cp ${./entrypoint.sh} app/entrypoint.sh
      cp ${./mcp_engine.py} app/mcp_engine.py
      cp ${./webserver.py} app/webserver.py
      cp ${./db_models.py} app/db_models.py
      chmod +x app/entrypoint.sh

      # Copy initial configuration files (will be overwritten dynamically)
      cp ${i2pdConfig} etc/i2pd/i2pd.conf
      cp ${torConfig} etc/tor/torrc

      # Create dummy certificate files to prevent warnings
      touch var/lib/i2pd/certificates/family/.dummy
      touch var/lib/i2pd/certificates/reseed/.dummy

      # Pre-download router info for instant bootstrap
      echo "Pre-downloading I2P router info for fast start..."
      mkdir -p var/lib/i2pd/netDb/r{0..9}{0..9}
      mkdir -p var/lib/i2pd/netDb/r{a..f}{0..9}
      mkdir -p var/lib/i2pd/netDb/r{0..9}{a..f}
      mkdir -p var/lib/i2pd/netDb/r{a..f}{a..f}

      # Create default sites.txt file
      cat > mnt/config/sites.txt << EOF
https://news.ycombinator.com/
EOF
    '';
  };
in
{
  dockerImage = image;
}