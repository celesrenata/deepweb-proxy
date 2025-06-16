{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = with pkgs; [
    python3
    python3Packages.requests
    python3Packages.beautifulsoup4
    python3Packages.fastapi
    python3Packages.uvicorn
    python3Packages.minio
    python3Packages.pip
    python3Packages.pymysql
    python3Packages.sqlalchemy
    python3Packages.cryptography
    python3Packages.python-dateutil
    python3Packages.pillow
    python3Packages.urllib3
    python3Packages.pysocks
    file
    tor
    openssl
    i2pd
    socat
    netcat-gnu
    nettools
    # Additional tools for dynamic reseed discovery
    curl
    findutils
    gnutar
    gzip
    iproute2
    procps
  ];

  shellHook = ''
    # Function to discover working reseed URLs
    discover_working_reseed_urls() {
      echo "=== Discovering Working I2P Reseed URLs ==="

      local discovered_urls=()
      local default_urls=(
        "https://reseed.diva.exchange/"
        "https://reseed.i2pgit.org/"
        "https://i2p.novg.net/"
        "https://reseed.memcpy.io/"
        "https://i2pseed.creativecowpat.net:8443/"
        "https://reseed.onion.im/"
        "https://reseed.atomike.ninja/"
        "https://banana.incognet.io/"
        "https://reseed.i2p-projekt.de/"
        "https://reseed.i2p.vzaws.com:8443/"
        "https://download.xxlspeed.com/"
        "https://reseed-fr.i2pd.xyz/"
        "https://reseed.akru.me/"
        "https://r31453.ovh.net/static_media/files/netDb/"
        "https://netdb.i2p2.no/"
        "https://reseed.i2p2.no/"
        "https://uk.reseed.i2p2.no:444/"
        "https://us.reseed.i2p2.no:444/"
        "https://ru.reseed.i2p2.no:444/"
        "https://netdb.family-vlitvinenko.tk/"
        "https://ieb9oopo.mooo.com/"
        "https://netdb.i2p.rocks/"
        "https://reseed.i2p.am/"
      )

      echo "Testing ''${#default_urls[@]} potential reseed servers..."

      for url in "''${default_urls[@]}"; do
        echo -n "Testing $url... "

        if timeout 8 curl -s --head --max-time 6 --connect-timeout 4 "$url" >/dev/null 2>&1; then
          echo "✓ Working"
          discovered_urls+=("$url")
        elif timeout 8 curl -s --max-time 6 --connect-timeout 4 "$url" | head -c 100 >/dev/null 2>&1; then
          echo "✓ Working (HEAD failed, GET succeeded)"
          discovered_urls+=("$url")
        else
          echo "✗ Failed"
        fi
      done

      echo "Found ''${#discovered_urls[@]} working reseed servers"

      if [ ''${#discovered_urls[@]} -eq 0 ]; then
        echo "⚠ No working reseed servers found - using fallback URLs"
        discovered_urls=("https://reseed.i2p-projekt.de/" "https://reseed.memcpy.io/")
      fi

      # Return the discovered URLs as comma-separated string
      local reseed_list=$(IFS=','; echo "''${discovered_urls[*]}")
      echo "$reseed_list"
    }

    # Create config directories
    mkdir -p .dev/{tor,i2pd,config}

    # Create optimized Tor config
    cat > .dev/tor/torrc << EOF
SocksPort 127.0.0.1:9050
DataDirectory $(pwd)/.dev/tor/data
RunAsDaemon 0
Log notice stdout
ConnectionPadding 0
ReducedConnectionPadding 1
CircuitBuildTimeout 10
LearnCircuitBuildTimeout 0
ClientOnly 1
ExitRelay 0
ExitPolicy reject *:*
EOF

    # Discover working reseed URLs
    echo "Discovering working I2P reseed servers..."
    RESEED_URLS=$(discover_working_reseed_urls)
    echo "Using reseed URLs: $RESEED_URLS"

    # Create optimized I2P config with discovered URLs (fixed deprecated options)
    cat > .dev/i2pd/i2pd.conf << EOF
ipv4 = true
ipv6 = false
notransit = true
floodfill = false
nat = true
datadir = $(pwd)/.dev/i2pd/data

# HTTP Proxy
httpproxy.enabled = true
httpproxy.address = 127.0.0.1
httpproxy.port = 4444
httpproxy.outproxy = http://false.i2p

# SAM interface
sam.enabled = true
sam.address = 127.0.0.1
sam.port = 7656

# Web console
http.enabled = true
http.address = 127.0.0.1
http.port = 7070

log = stdout
loglevel = error

# Dynamic reseed configuration with discovered working servers
reseed.verify = false
reseed.floodfill = false
reseed.threshold = 15
reseed.urls = $RESEED_URLS

# Network configuration
bandwidth = 512
share = 25

# Transport settings - FIXED: removed deprecated ntcp.enabled
ntcp2.enabled = true
ntcp2.port = 0
ssu2.enabled = true
ssu2.port = 0

# Conservative limits
limits.transittunnels = 50
limits.openfiles = 4096

# Precomputation
precomputation.elgamal = true

# Bootstrap help
exploratory.inbound.length = 2
exploratory.outbound.length = 2
exploratory.inbound.quantity = 3
exploratory.outbound.quantity = 3
EOF

    # Create data directories
    mkdir -p .dev/tor/data .dev/i2pd/data
    chmod 700 .dev/tor/data .dev/i2pd/data

    # Function to test if service is running
    check_service() {
      local service_name=$1
      local port=$2
      if ss -nlt | grep -q ":$port "; then
        echo "✓ $service_name is running on port $port"
        return 0
      else
        echo "⚠ $service_name not running on port $port"
        return 1
      fi
    }

    # Start services if not running
    if ! pgrep -f "tor.*$(pwd)/.dev/tor/torrc" > /dev/null; then
      echo "Starting Tor with optimized config..."
      tor -f .dev/tor/torrc &
      sleep 3

      # Wait for Tor to be ready
      for i in {1..30}; do
        if check_service "Tor" 9050; then
          break
        fi
        sleep 1
      done
    else
      echo "Tor already running"
    fi

    if ! pgrep -f "i2pd.*$(pwd)/.dev/i2pd/i2pd.conf" > /dev/null; then
      echo "Starting I2P with dynamic reseed config..."
      echo "Note: I2P may take several minutes to bootstrap..."
      i2pd --conf=.dev/i2pd/i2pd.conf &
      sleep 10

      # Wait for I2P ports to be ready
      for i in {1..60}; do
        if check_service "I2P HTTP Proxy" 4444 && check_service "I2P Console" 7070; then
          break
        fi
        sleep 2
      done
    else
      echo "I2P already running"
    fi

    echo "Development environment ready!"
    echo "Tor SOCKS proxy: 127.0.0.1:9050"
    echo "I2P HTTP proxy: 127.0.0.1:4444"
    echo "I2P console: http://0.0.0.0:7070"

    # Enhanced connectivity testing
    echo "Testing proxy connectivity..."

    # Test Tor
    if timeout 10 curl -s --proxy socks5h://127.0.0.1:9050 http://httpbin.org/ip > /dev/null 2>&1; then
      echo "✓ Tor proxy working"
    else
      echo "⚠ Tor proxy not ready yet"
    fi

    # Test I2P with more patience
    echo "Testing I2P proxy (may take time for bootstrap)..."
    i2p_working=false
    for i in {1..6}; do
      if timeout 15 curl -s --proxy 127.0.0.1:4444 --max-time 12 http://httpbin.org/ip > /dev/null 2>&1; then
        echo "✓ I2P proxy working"
        i2p_working=true
        break
      else
        echo "⚠ I2P proxy test $i/6 failed (may still be bootstrapping...)"
        if [ $i -lt 6 ]; then
          sleep 30
        fi
      fi
    done

    if [ "$i2p_working" = false ]; then
      echo "⚠ I2P proxy not ready yet - check console at http://0.0.0.0:7070"
      echo "  I2P may need 5-10 minutes to fully bootstrap on first run"
    fi

    # Provide helpful aliases
    alias check-proxies='echo "Checking proxy status..."; timeout 5 curl -s --proxy socks5h://127.0.0.1:9050 http://httpbin.org/ip && echo "✓ Tor working" || echo "✗ Tor failed"; timeout 10 curl -s --proxy 127.0.0.1:4444 http://httpbin.org/ip && echo "✓ I2P working" || echo "✗ I2P failed"'
    alias i2p-console='echo "I2P Console: http://0.0.0.0:7070"'
    alias restart-i2p='pkill -f "i2pd.*$(pwd)/.dev/i2pd/i2pd.conf"; sleep 2; i2pd --conf=.dev/i2pd/i2pd.conf &'

    echo ""
    echo "Available commands:"
    echo "  check-proxies  - Test proxy connectivity"
    echo "  i2p-console    - Show I2P console URL"
    echo "  restart-i2p    - Restart I2P with current config"
  '';
}