#!/usr/bin/env bash
set -e

echo "=== DeepWeb Proxy Starting ==="

# Global variables
TOR_PID=""
I2P_PID=""
WEBSERVER_PID=""
MCP_PID=""
SHUTDOWN_REQUESTED=false

# At the beginning of entrypoint.sh, add this function:
set_enhanced_system_limits() {
    echo "=== Setting Enhanced System Limits ==="

    # Use environment variables if set, otherwise use defaults
    local target_limit=${ULIMIT_NOFILE:-65536}
    local i2p_limit=${I2P_OPENFILES_LIMIT:-32768}

    echo "Target ulimit: $target_limit"
    echo "I2P openfiles limit: $i2p_limit"

    # Method 1: Set ulimit with fallback
    if ulimit -n $target_limit 2>/dev/null; then
        echo "✓ Set ulimit to $target_limit"
    else
        echo "⚠ Could not set ulimit to $target_limit, trying incremental limits..."
        for limit in 32768 16384 8192 4096; do
            if ulimit -n $limit 2>/dev/null; then
                echo "✓ Set ulimit to $limit"
                target_limit=$limit
                break
            fi
        done
    fi

    # Method 2: Use prlimit if available
    if command -v prlimit >/dev/null 2>&1; then
        prlimit --nofile=$target_limit --pid=$$ 2>/dev/null && echo "✓ prlimit set to $target_limit"
    fi

    # Verify current limits
    local current_soft=$(ulimit -Sn)
    local current_hard=$(ulimit -Hn)
    echo "Current limits - Soft: $current_soft, Hard: $current_hard"

    # Export for later use
    export CURRENT_ULIMIT=$current_soft
    export I2P_EFFECTIVE_LIMIT=$i2p_limit

    # Fail if limits are too low
    if [ "$current_soft" -lt 2048 ]; then
        echo "❌ CRITICAL: File descriptor limit too low ($current_soft) for I2P"
        echo "   Container may need --privileged or proper resource limits"
        return 1
    fi

    return 0
}

# Call this function at the very beginning of your script
set_enhanced_system_limits || exit 1

# Add this function after set_enhanced_system_limits()

setup_ssl_certificates() {
    echo "=== Setting up Nix SSL certificates ==="

    # Find the exact Nix CA bundle path
    CA_BUNDLE=$(find /nix/store -name "ca-bundle.crt" -path "*/nss-cacert*/etc/ssl/certs/ca-bundle.crt" 2>/dev/null | head -1)

    if [ -z "$CA_BUNDLE" ]; then
        # Fallback: find any ca-bundle.crt
        CA_BUNDLE=$(find /nix/store -name "ca-bundle.crt" 2>/dev/null | head -1)
    fi

    if [ -n "$CA_BUNDLE" ] && [ -f "$CA_BUNDLE" ]; then
        # Export all relevant environment variables
        export SSL_CERT_FILE="$CA_BUNDLE"
        export CURL_CA_BUNDLE="$CA_BUNDLE"
        export SSL_CERT_DIR="$(dirname "$CA_BUNDLE")"
        export REQUESTS_CA_BUNDLE="$CA_BUNDLE"
        export NIX_SSL_CERT_FILE="$CA_BUNDLE"
        export PYTHONHTTPSVERIFY=1

        # Create symlinks to standard locations
        mkdir -p /etc/ssl/certs
        ln -sf "$CA_BUNDLE" /etc/ssl/certs/ca-certificates.crt 2>/dev/null || true
        ln -sf "$CA_BUNDLE" /etc/ssl/certs/ca-bundle.crt 2>/dev/null || true

        echo "✓ SSL certificates configured: $CA_BUNDLE"

        # Test SSL connectivity
        if curl --max-time 5 -s https://httpbin.org/ip >/dev/null 2>&1; then
            echo "✓ SSL verification working"
        else
            echo "⚠ SSL verification test failed"
        fi

        return 0
    else
        echo "❌ No CA bundle found in Nix store"
        return 1
    fi
}


setup_fallback_certificates() {
    local ca_bundle="$1"

    echo "Setting up fallback certificate configuration..."

    # Create a local certificate directory
    mkdir -p /tmp/certs

    if [ -n "$ca_bundle" ] && [ -f "$ca_bundle" ]; then
        # Copy the CA bundle to a local location
        cp "$ca_bundle" /tmp/certs/ca-bundle.crt
        chmod 644 /tmp/certs/ca-bundle.crt
    else
        # Create a minimal certificate bundle using openssl
        echo "Creating minimal certificate bundle..."
        echo "" > /tmp/certs/ca-bundle.crt

        # Try to extract certificates from the system
        if command -v update-ca-certificates >/dev/null 2>&1; then
            update-ca-certificates 2>/dev/null || true
        fi
    fi

    # Set fallback environment variables
    export SSL_CERT_FILE="/tmp/certs/ca-bundle.crt"
    export CURL_CA_BUNDLE="/tmp/certs/ca-bundle.crt"

    # Create a curl config file with certificate settings
    cat > /tmp/certs/curl-config << EOF
# Curl configuration for SSL
capath=/tmp/certs/
cacert=/tmp/certs/ca-bundle.crt
EOF

    export CURL_HOME="/tmp/certs"
}

test_ssl_connectivity() {
    echo "Testing SSL connectivity..."

    # Test with different methods
    local test_urls=(
        "https://httpbin.org/ip"
        "https://check.torproject.org/api/ip"
        "https://www.google.com"
    )

    local ssl_working=false

    for url in "${test_urls[@]}"; do
        echo "Testing SSL with: $url"

        # Test with curl
        if curl --max-time 10 -s "$url" >/dev/null 2>&1; then
            echo "✓ SSL working with curl for $url"
            ssl_working=true
            break
        elif curl --max-time 10 -s -k "$url" >/dev/null 2>&1; then
            echo "⚠ SSL working with -k (insecure) for $url"
        else
            echo "✗ SSL failed for $url"
        fi
    done

    # Test with Tor proxy
    if [ "$ssl_working" = true ]; then
        echo "Testing SSL through Tor proxy..."
        if curl --proxy socks5h://127.0.0.1:9050 --max-time 15 -s "https://check.torproject.org/api/ip" >/dev/null 2>&1; then
            echo "✓ SSL working through Tor"
        elif curl --proxy socks5h://127.0.0.1:9050 --max-time 15 -s -k "https://check.torproject.org/api/ip" >/dev/null 2>&1; then
            echo "⚠ SSL working through Tor with -k (insecure)"
        else
            echo "✗ SSL failed through Tor"
        fi
    fi

    return 0
}

# Function to set system limits properly
set_system_limits() {
    echo "=== Setting System Limits ==="

    # Set file descriptor limits - be more aggressive
    echo "Setting file descriptor limits..."

    # Try multiple approaches to increase file descriptor limits
    ulimit -n 65536 2>/dev/null || {
        echo "⚠ Could not set ulimit to 65536, trying systemd approach..."

        # Try to modify systemd limits if available
        if [ -f /etc/systemd/system.conf ]; then
            echo "DefaultLimitNOFILE=65536" >> /etc/systemd/system.conf 2>/dev/null || true
        fi

        # Try prlimit if available
        prlimit --nofile=65536 --pid=$$ 2>/dev/null || true

        # Fall back to smaller limits
        ulimit -n 8192 2>/dev/null || {
            ulimit -n 4096 2>/dev/null || {
                ulimit -n 1024 2>/dev/null || echo "⚠ Could not set any file descriptor limit"
            }
        }
    }

    # Set other limits
    ulimit -c 0 2>/dev/null || echo "⚠ Could not set core dump limit"

    # Try to increase process limits
    ulimit -u 4096 2>/dev/null || echo "⚠ Could not set process limit"

    echo "Current file descriptor limit: $(ulimit -n)"
    echo "Current process limit: $(ulimit -u)"
    echo "Current core dump limit: $(ulimit -c)"
}

# Function to create I2P directory structure while preserving existing data
create_i2p_directories() {
    echo "Setting up I2P directory structure (preserving existing data)..."

    # Check if we have existing router data
    local existing_routes=0
    if [ -d "/var/lib/i2pd/netDb" ]; then
        existing_routes=$(find /var/lib/i2pd/netDb -name "*.dat" 2>/dev/null | wc -l)
        echo "Found $existing_routes existing router entries"
    fi

    # Create base directories only if they don't exist
    mkdir -p /var/lib/i2pd/{certificates/{family,reseed},peerProfiles,addressbook}

    # Only create netDb subdirectories if we don't have existing data
    if [ "$existing_routes" -eq 0 ]; then
        echo "No existing routes found - creating fresh netDb structure..."
        mkdir -p /var/lib/i2pd/netDb

        # Create netDb subdirectories
        for prefix in {0..9} {a..f}; do
            for suffix in {0..9} {a..f}; do
                mkdir -p "/var/lib/i2pd/netDb/r${prefix}${suffix}"
            done
        done
        echo "✓ Fresh I2P netDb structure created"
    else
        echo "✓ Preserving existing I2P routes ($existing_routes entries)"

        # Just ensure the base netDb directory exists, don't recreate subdirs
        mkdir -p /var/lib/i2pd/netDb

        # Only create missing subdirectories, don't touch existing ones
        for prefix in {0..9} {a..f}; do
            for suffix in {0..9} {a..f}; do
                [ ! -d "/var/lib/i2pd/netDb/r${prefix}${suffix}" ] && mkdir -p "/var/lib/i2pd/netDb/r${prefix}${suffix}"
            done
        done
    fi

    # Set proper permissions without affecting existing files
    chmod 755 /var/lib/i2pd
    chmod 755 /var/lib/i2pd/certificates 2>/dev/null || true
    chmod 755 /var/lib/i2pd/certificates/family 2>/dev/null || true
    chmod 755 /var/lib/i2pd/certificates/reseed 2>/dev/null || true
    chmod 755 /var/lib/i2pd/netDb 2>/dev/null || true
    chmod 755 /var/lib/i2pd/peerProfiles 2>/dev/null || true
    chmod 755 /var/lib/i2pd/addressbook 2>/dev/null || true

    # Create dummy files only if they don't exist
    [ ! -f /var/lib/i2pd/certificates/family/.dummy ] && touch /var/lib/i2pd/certificates/family/.dummy
    [ ! -f /var/lib/i2pd/certificates/reseed/.dummy ] && touch /var/lib/i2pd/certificates/reseed/.dummy

    echo "✓ I2P directory structure ready"
}

# Function to check if we should skip reseed due to existing good routes
should_skip_reseed() {
    local min_routes=${I2P_MIN_ROUTES_FOR_SKIP_RESEED:-50}
    local route_count=0

    if [ -d "/var/lib/i2pd/netDb" ]; then
        route_count=$(find /var/lib/i2pd/netDb -name "*.dat" -mtime -7 2>/dev/null | wc -l)
    fi

    echo "Found $route_count recent routes (minimum: $min_routes)"

    if [ "$route_count" -ge "$min_routes" ]; then
        echo "✓ Sufficient cached routes available - I2P should start faster"
        return 0
    else
        echo "⚠ Insufficient routes - may need reseed"
        return 1
    fi
}

# Function to create other necessary directories
create_directories() {
    echo "Creating necessary directories..."

    # Create Tor directory
    mkdir -p /var/lib/tor
    chmod 700 /var/lib/tor

    # Create I2P directories
    create_i2p_directories

    # Create other directories
    mkdir -p /run /tmp /mnt/config
    chmod 1777 /tmp
    chmod 755 /run /mnt/config

    echo "✓ All directories created"
}

# Function to cleanup processes
cleanup_processes() {
    if [ "$SHUTDOWN_REQUESTED" = true ]; then
        echo "Cleaning up processes..."

        # Kill I2P processes
        pkill -f "i2pd" 2>/dev/null || true
        sleep 1

        # Kill Tor processes
        pkill -f "tor" 2>/dev/null || true
        sleep 1

        # Clean up lock files and sockets
        rm -f /var/lib/i2pd/i2pd.pid /var/lib/i2pd/*.pid /var/lib/i2pd/*.sock 2>/dev/null || true
        rm -f /var/lib/tor/lock 2>/dev/null || true
    else
        echo "Initial cleanup - removing stale files only..."
        # Only remove stale files, don't kill processes during startup
        rm -f /var/lib/i2pd/i2pd.pid /var/lib/i2pd/*.pid /var/lib/i2pd/*.sock 2>/dev/null || true
        rm -f /var/lib/tor/lock 2>/dev/null || true
    fi
}


# Add this function to force reseed
force_i2p_reseed() {
    echo "=== Forcing I2P Reseed ==="

    # Stop I2P if running
    if [ -n "$I2P_PID" ] && kill -0 "$I2P_PID" 2>/dev/null; then
        echo "Stopping I2P for forced reseed..."
        kill -TERM "$I2P_PID" 2>/dev/null || true
        sleep 5
        kill -KILL "$I2P_PID" 2>/dev/null || true
    fi

    # Clear existing but broken netDb
    echo "Clearing broken netDb..."
    rm -rf /var/lib/i2pd/netDb/*

    # Recreate netDb structure
    create_i2p_directories

    # Get working reseed URLs
    local reseed_urls
    reseed_urls=$(discover_working_reseed_urls_enhanced)

    # Create aggressive bootstrap config
    create_bootstrap_config "$reseed_urls"

    # Start I2P in bootstrap mode
    echo "Starting I2P in aggressive bootstrap mode..."
    i2pd --conf=/etc/i2pd/i2pd.conf --datadir=/var/lib/i2pd --reseed.threshold=0 2>&1 &
    I2P_PID=$!

    # Wait for bootstrap with extended timeout
    wait_for_bootstrap 600  # 10 minutes
}

# Also update wait_for_services_ready to be more strict
wait_for_services_ready() {
    echo "=== Waiting for Services to be Ready ==="

    local tor_ready=false
    local i2p_ready=false
    local max_wait=600  # Increase to 10 minutes
    local waited=0

    echo "Waiting for Tor and I2P to be fully operational..."

    while [ $waited -lt $max_wait ]; do
        # Check Tor more thoroughly
        if [ "$tor_ready" = false ]; then
            if curl -s --proxy socks5h://127.0.0.1:9050 --max-time 8 https://check.torproject.org/api/ip >/dev/null 2>&1; then
                echo "✓ Tor is ready and working"
                tor_ready=true
            else
                echo "⏳ Tor not ready yet (${waited}s elapsed)..."
            fi
        fi

        # Check I2P more thoroughly
        if [ "$i2p_ready" = false ]; then
            # Check if I2P console is responsive and has sufficient routers
            if curl -s --max-time 5 http://0.0.0.0:7070/netdb >/dev/null 2>&1; then
                # Try to get router count and ensure it's sufficient
                local router_count=$(curl -s --max-time 5 http://0.0.0.0:7070/netdb 2>/dev/null | grep -o '[0-9]\+' | head -1)
                if [ -n "$router_count" ] && [ "$router_count" -gt 10 ]; then
                    echo "✓ I2P console responsive with $router_count routers"
                    i2p_ready=true
                else
                    echo "⏳ I2P console up but insufficient routers ($router_count) (${waited}s elapsed)..."
                fi
            else
                echo "⏳ I2P console not ready yet (${waited}s elapsed)..."
            fi
        fi

        # Exit if both are ready
        if [ "$tor_ready" = true ] && [ "$i2p_ready" = true ]; then
            echo "✓ All services are ready!"
            return 0
        fi

        sleep 15  # Check every 15 seconds instead of 10
        waited=$((waited + 15))
    done

    # Timeout reached - this is now a FAILURE
    echo "❌ TIMEOUT waiting for services after ${max_wait}s:"
    echo "  Tor ready: $tor_ready"
    echo "  I2P ready: $i2p_ready"
    return 1  # Return failure instead of continuing
}

# Function to start services with proper sequencing
start_all_services() {
    echo "=== Starting All Services ==="

    # Start Tor first (faster to start)
    echo "Starting Tor..."
    tor -f /etc/tor/torrc &
    TOR_PID=$!
    echo "Tor started with PID: $TOR_PID"

    # Start I2P
    echo "Starting I2P..."
    i2pd --conf=/etc/i2pd/i2pd.conf --datadir=/var/lib/i2pd 2>&1 &
    I2P_PID=$!
    echo "I2P started with PID: $I2P_PID"

    # Give services initial time to start
    echo "Giving services 60 seconds to initialize..."
    sleep 60

    # Wait for services to be ready before proceeding - MANDATORY
    echo "Waiting for proxy services to be fully operational..."
    if ! wait_for_services_ready; then
        echo "❌ CRITICAL: Proxy services failed to initialize properly"
        echo "Cannot start crawler without working proxies for .onion/.i2p sites"
        exit 1
    fi

    # Additional verification before starting crawler
    echo "Performing final proxy verification..."
    if ! verify_proxy_functionality; then
        echo "❌ CRITICAL: Proxy verification failed"
        exit 1
    fi

    # Now start the web applications
    echo "Starting web server..."
    cd /app
    python3 webserver.py &
    WEBSERVER_PID=$!
    echo "Web server started with PID: $WEBSERVER_PID"

    # CRITICAL: Add delay before starting crawler
    echo "Waiting additional 30 seconds before starting crawler..."
    sleep 30

    # Finally start the crawler (now that proxies are VERIFIED ready)
    echo "Starting MCP crawler (proxies verified and ready)..."
    python3 mcp_engine.py &
    MCP_PID=$!
    echo "MCP crawler started with PID: $MCP_PID"

    echo "✓ All services started successfully with verified proxy functionality"
}

# Add this new function for final verification
verify_proxy_functionality() {
    echo "=== Final Proxy Verification ==="

    local tor_working=false
    local i2p_working=false

    # Test Tor with actual .onion site
    echo "Testing Tor with .onion site..."
    if curl -s --proxy socks5h://127.0.0.1:9050 --max-time 10 "http://3g2upl4pq6kufc4m.onion" >/dev/null 2>&1; then
        echo "✓ Tor proxy verified with .onion site"
        tor_working=true
    else
        echo "⚠ Tor proxy test failed"
    fi

    # Test I2P with actual .i2p site
    echo "Testing I2P with .i2p site..."
    if curl -s --proxy http://127.0.0.1:4444 --max-time 15 "http://stats.i2p/" >/dev/null 2>&1; then
        echo "✓ I2P proxy verified with .i2p site"
        i2p_working=true
    else
        echo "⚠ I2P proxy test failed"
    fi

    # Require at least one working proxy
    if [ "$tor_working" = false ] && [ "$i2p_working" = false ]; then
        echo "❌ NO WORKING PROXIES - Cannot proceed"
        return 1
    fi

    if [ "$tor_working" = true ] && [ "$i2p_working" = true ]; then
        echo "✓ Both Tor and I2P proxies verified and working"
    elif [ "$tor_working" = true ]; then
        echo "✓ Tor proxy working (I2P may be limited)"
    else
        echo "✓ I2P proxy working (Tor may be limited)"
    fi

    return 0
}


# Fixed version of discover_working_reseed_urls_enhanced
discover_working_reseed_urls_enhanced() {
    # Redirect all echo output to stderr to avoid config corruption
    exec 3>&2  # Save stderr
    exec 2>/dev/null  # Suppress curl errors temporarily

    local discovered_urls=()
    local priority_urls=(
        "https://reseed.diva.exchange/"
        "https://reseed.i2pgit.org/"
        "https://i2p.novg.net/"
        "https://reseed.memcpy.io/"
        "https://i2pseed.creativecowpat.net:8443/"
        "https://reseed.onion.im/"
        "https://reseed.atomike.ninja/"
        "https://banana.incognet.io/"
    )

    # Test URLs without output
    for url in "${priority_urls[@]}"; do
        if timeout 10 curl -sSL --head --max-time 8 "$url" >/dev/null 2>&1; then
            discovered_urls+=("$url")
        elif timeout 15 wget --spider --timeout=8 --tries=1 "$url" >/dev/null 2>&1; then
            discovered_urls+=("$url")
        fi

        # Stop after finding 3 working servers
        [ ${#discovered_urls[@]} -ge 3 ] && break
    done

    # Restore stderr
    exec 2>&3
    exec 3>&-

    if [ ${#discovered_urls[@]} -eq 0 ]; then
        # Use hardcoded fallbacks
        discovered_urls=("https://reseed.i2p-projekt.de/" "https://reseed.memcpy.io/")
    fi

    # ONLY output the final result - no debug messages
    local reseed_list=$(IFS=','; echo "${discovered_urls[*]}")
    echo "$reseed_list"
}

# Fixed bootstrap config creation
create_bootstrap_config() {
    local reseed_urls="$1"

    # Log to stderr, not stdout
    echo "Creating I2P bootstrap configuration..." >&2

    cat > /etc/i2pd/i2pd.conf << EOF
# Bootstrap Configuration
ipv4 = true
ipv6 = false
notransit = true
floodfill = false
nat = true

# HTTP Proxy
httpproxy.enabled = true
httpproxy.address = 0.0.0.0
httpproxy.port = 4444

# Console
http.enabled = true
http.address = 0.0.0.0
http.port = 7070

# Logging
log = stdout
loglevel = warn

# Reseed configuration
reseed.verify = false
reseed.floodfill = false
reseed.threshold = 5
reseed.urls = $reseed_urls

# Network settings
bandwidth = 256
share = 15

# Transport protocols
ntcp2.enabled = true
ntcp2.port = 0
ssu2.enabled = true
ssu2.port = 0

# Limits
limits.transittunnels = 10
limits.openfiles = ${I2P_EFFECTIVE_LIMIT:-4096}

# Bootstrap tunnels
exploratory.inbound.length = 2
exploratory.outbound.length = 2
exploratory.inbound.quantity = 2
exploratory.outbound.quantity = 2

# Performance
precomputation.elgamal = true
EOF

    echo "✓ Bootstrap configuration created" >&2
}

# Wait for bootstrap completion
wait_for_bootstrap() {
    local max_wait=${1:-300}
    local waited=0
    local check_interval=10

    echo "Waiting for I2P bootstrap (max ${max_wait}s)..."

    while [ $waited -lt $max_wait ]; do
        # Check if I2P process is alive
        if ! kill -0 "$I2P_PID" 2>/dev/null; then
            echo "❌ I2P process died during bootstrap"
            return 1
        fi

        # Check route count
        local route_count=0
        if [ -d "/var/lib/i2pd/netDb" ]; then
            route_count=$(find /var/lib/i2pd/netDb -name "*.dat" 2>/dev/null | wc -l)
        fi

        echo "Bootstrap progress: ${route_count} routes downloaded (${waited}s elapsed)"

        # Check if we have enough routes to proceed
        if [ "$route_count" -ge 10 ]; then
            echo "✓ Bootstrap successful - $route_count routes available"

            # Test console
            if curl -s --max-time 5 "http://0.0.0.0:7070" | grep -i "i2p" >/dev/null 2>&1; then
                echo "✓ I2P console responding"
                return 0
            fi
        fi

        sleep $check_interval
        waited=$((waited + check_interval))
    done

    echo "⚠ Bootstrap timeout after ${max_wait}s (${route_count} routes)"
    return 1
}

# Function to handle shutdown gracefully
handle_shutdown() {
    if [ "$SHUTDOWN_REQUESTED" = true ]; then
        echo "Shutdown already in progress, ignoring additional signals"
        return
    fi

    SHUTDOWN_REQUESTED=true
    echo "=== Shutting down gracefully ==="

    # Kill MCP engine first
    if [ -n "$MCP_PID" ] && kill -0 "$MCP_PID" 2>/dev/null; then
        echo "Stopping MCP engine..."
        kill "$MCP_PID" 2>/dev/null || true
        wait "$MCP_PID" 2>/dev/null || true
    fi

    # Kill web server
    if [ -n "$WEBSERVER_PID" ] && kill -0 "$WEBSERVER_PID" 2>/dev/null; then
        echo "Stopping web server..."
        kill "$WEBSERVER_PID" 2>/dev/null || true
        wait "$WEBSERVER_PID" 2>/dev/null || true
    fi

    # Kill I2P
    if [ -n "$I2P_PID" ] && kill -0 "$I2P_PID" 2>/dev/null; then
        echo "Stopping I2P..."
        kill "$I2P_PID" 2>/dev/null || true
        wait "$I2P_PID" 2>/dev/null || true
    fi

    # Kill Tor
    if [ -n "$TOR_PID" ] && kill -0 "$TOR_PID" 2>/dev/null; then
        echo "Stopping Tor..."
        kill "$TOR_PID" 2>/dev/null || true
        wait "$TOR_PID" 2>/dev/null || true
    fi

    echo "Shutdown complete"
    exit 0
}

# Function to setup signal handling
setup_signal_handling() {
    trap 'handle_shutdown' SIGTERM SIGINT
    echo "Signal handling enabled"
}

# Function to create optimized Tor configuration
create_tor_config() {
    echo "Creating optimized Tor configuration..."

    cat > /etc/tor/torrc << 'EOF'
SocksPort 0.0.0.0:9050
DataDirectory /var/lib/tor
RunAsDaemon 0
Log notice stdout

# Performance optimizations
ConnectionPadding 0
ReducedConnectionPadding 1
CircuitBuildTimeout 15
LearnCircuitBuildTimeout 0
MaxCircuitDirtiness 600
NewCircuitPeriod 30
MaxClientCircuitsPending 16
KeepalivePeriod 60

# Security settings
ExitRelay 0
ExitPolicy reject *:*
ClientOnly 1

# Resource limits for container
MaxMemInQueues 256 MB
ConstrainedSockets 1
ConstrainedSockSize 8192
EOF
}

# Function to discover working reseed URLs (fixed to return clean output)
discover_working_reseed_urls() {
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
        "https://download.xxlspeed.com/"
        "https://reseed-fr.i2pd.xyz/"
        "https://reseed.akru.me/"
        "https://netdb.i2p.rocks/"
    )

    # Test URLs silently and collect working ones
    for url in "${default_urls[@]}"; do
        if timeout 8 curl -s --head --max-time 6 --connect-timeout 4 "$url" >/dev/null 2>&1; then
            discovered_urls+=("$url")
        elif timeout 8 curl -s --max-time 6 --connect-timeout 4 "$url" | head -c 100 >/dev/null 2>&1; then
            discovered_urls+=("$url")
        fi
    done

    if [ ${#discovered_urls[@]} -eq 0 ]; then
        # Fallback to your known working servers
        discovered_urls=(
            "https://reseed.diva.exchange/"
            "https://reseed.memcpy.io/"
            "https://reseed.i2pgit.org/"
            "https://i2p.novg.net/"
        )
    fi

    # Return ONLY the comma-separated list, no other output
    local reseed_list=$(IFS=','; echo "${discovered_urls[*]}")
    echo "$reseed_list"
}

# Function to create bootstrap-friendly I2P config by default
create_i2p_config() {
    echo "Creating I2P configuration..."

    local current_limit=$(ulimit -n)
    if [ -n "$I2P_EFFECTIVE_LIMIT" ]; then
        i2p_openfiles=$I2P_EFFECTIVE_LIMIT
    else
        current_limit=$(ulimit -n)
        # Use at least 4096 for I2P, or 75% of system limit, whichever is higher
        i2p_openfiles=$(( current_limit > 4096 ? current_limit * 3 / 4 : 4096 ))
    fi

    # Ensure minimum of 4096 for I2P
    [ $i2p_openfiles -lt 4096 ] && i2p_openfiles=4096

    echo "Setting I2P openfiles limit to: $i2p_openfiles (system limit: $(ulimit -n))"

    # Ensure minimum viable limits
    if [ "$i2p_openfiles" -lt 64 ]; then
        i2p_openfiles=64
    fi

    if [ "$i2p_openfiles" -gt 4096 ]; then
        i2p_openfiles=4096
    fi

    echo "Setting I2P openfiles limit to: $i2p_openfiles (system limit: $current_limit)"

    # RESPECT GENTLE MODE ENVIRONMENT VARIABLES
    local reseed_threshold=5
    local reseed_aggressive="false"

    # Check gentle mode setting from environment
    if [ "${I2P_GENTLE_MODE:-true}" = "true" ]; then
        reseed_threshold=25  # Higher threshold = more patient
        echo "✓ Using GENTLE I2P configuration (patient mode enabled)"
        echo "  I2P_GENTLE_MODE=$I2P_GENTLE_MODE"
        echo "  I2P_BOOTSTRAP_PATIENCE=$I2P_BOOTSTRAP_PATIENCE"
        echo "  I2P_FORCE_BOOTSTRAP=$I2P_FORCE_BOOTSTRAP"
    else
        # Only use aggressive if explicitly disabled
        if should_skip_reseed; then
            reseed_threshold=25
            echo "Using conservative reseed settings (have cached routes)"
        else
            reseed_threshold=5
            echo "Using aggressive reseed settings (gentle mode disabled)"
        fi
    fi

    # Discover working reseed URLs
    echo "Discovering working I2P reseed servers..."
    local reseed_urls
    reseed_urls=$(discover_working_reseed_urls)
    echo "Found reseed URLs: $reseed_urls"

    cat > /etc/i2pd/i2pd.conf << EOF
# GENTLE I2P configuration (respects environment variables)
ipv4 = true
ipv6 = false
notransit = false
floodfill = false
nat = true

# HTTP Proxy configuration
httpproxy.enabled = true
httpproxy.address = 0.0.0.0
httpproxy.port = 4444
httpproxy.addresshelper = true

# Web console
http.enabled = true
http.address = 0.0.0.0
http.port = 7070

# Logging
log = stdout
loglevel = warn

# GENTLE reseed configuration (respects I2P_GENTLE_MODE)
reseed.verify = false
reseed.floodfill = false
reseed.threshold = ${reseed_threshold}
reseed.urls = ${reseed_urls}

# GENTLE network settings
bandwidth = 512
share = 25

# Transport settings
ntcp2.enabled = true
ntcp2.port = 0
ssu2.enabled = true
ssu2.port = 0

# CONSERVATIVE file descriptor limits
limits.transittunnels = 50
limits.openfiles = ${i2p_openfiles}
limits.coresize = 0

# Enable precomputation
precomputation.elgamal = true

# GENTLE bootstrap settings
exploratory.inbound.length = 3
exploratory.outbound.length = 3
exploratory.inbound.quantity = 4
exploratory.outbound.quantity = 4

# Route persistence
persist.profiles = true
persist.addressbook = true

# Disable unused services
upnp.enabled = false
EOF
}

# Function to start Tor with proper error handling
start_tor() {
    echo "=== Starting Tor ==="

    # Setup Tor environment
    mkdir -p /var/lib/tor /run/tor
    chmod 700 /var/lib/tor
    chmod 755 /run/tor

    # Create container-friendly Tor config
    create_tor_config

    echo "Starting Tor..."
    tor -f /etc/tor/torrc &
    TOR_PID=$!
    echo "Tor started with PID: $TOR_PID"

    # Wait for Tor to be ready
    echo "Waiting for Tor to be ready..."
    for i in {1..30}; do
        if ss -nlt | grep -q ":9050 "; then
            echo "✓ Tor is ready on port 9050"
            return 0
        fi
        echo "Waiting for Tor... ($i/30)"
        sleep 2
    done

    echo "❌ Tor failed to start properly"
    return 1
}


# Enhanced wait function for Tor to have exit circuits
wait_for_tor_exit_circuits() {
    echo "Waiting for Tor to build exit circuits..." >&2
    local max_wait=300  # 5 minutes max
    local elapsed=0

    while [ $elapsed -lt $max_wait ]; do
        # Check if Tor control port is available (usually means circuits are ready)
        if timeout 5 curl -sSL --proxy socks5h://127.0.0.1:9050 --max-time 8 \
           --connect-timeout 5 "http://example.com" >/dev/null 2>&1; then
            echo "✓ Tor exit circuits are working" >&2
            return 0
        fi

        # Also check if we can reach a simple clearnet site
        if timeout 8 curl -sSL --proxy socks5h://127.0.0.1:9050 --max-time 6 \
           "https://httpbin.org/ip" >/dev/null 2>&1; then
            echo "✓ Tor exit circuits confirmed via httpbin" >&2
            return 0
        fi

        echo "Waiting for Tor exit circuits... (${elapsed}s elapsed)" >&2
        sleep 10
        elapsed=$((elapsed + 10))
    done

    echo "⚠ Timeout waiting for exit circuits, but proceeding anyway" >&2
    return 1
}

start_webserver() {
    echo "=== Starting Web Server ===" >&2

    # Set Python path
    export PYTHONPATH="/app:$PYTHONPATH"

    # Start the web server
    cd /app
    python3 -m uvicorn webserver:app --host 0.0.0.0 --port 8080 --log-level info &

    # Store the PID
    WEBSERVER_PID=$!
    echo "Web server started with PID: $WEBSERVER_PID" >&2

    # Wait a moment for startup
    sleep 3

    # Check if it's running
    if kill -0 $WEBSERVER_PID 2>/dev/null; then
        echo "✓ Web server is running on port 8080" >&2
        return 0
    else
        echo "✗ Web server failed to start" >&2
        return 1
    fi
}

start_crawler() {
    echo "=== Starting Python Crawler ===" >&2

    # Set Python path
    export PYTHONPATH="/app:$PYTHONPATH"

    # Start the crawler engine with the correct class name
    cd /app
    python3 -c "
import sys
sys.path.insert(0, '/app')
from mcp_engine import AIResearchCrawler

# Create and start the crawler
print('Initializing AI Research Crawler...')
crawler = AIResearchCrawler()
print('Starting crawler...')
crawler.crawl_sites()
" &

    # Store the PID
    CRAWLER_PID=$!
    echo "Crawler started with PID: $CRAWLER_PID" >&2

    # Wait a moment for startup
    sleep 5

    # Check if it's running
    if kill -0 $CRAWLER_PID 2>/dev/null; then
        echo "✓ Crawler is running" >&2
        return 0
    else
        echo "✗ Crawler failed to start" >&2
        return 1
    fi
}

monitor_services() {
    echo "=== Intelligent Service Monitor Started ===" >&2

    while true; do
        sleep 30  # Check every 30 seconds

        # Check web server by PORT, not just PID
        if netstat -tln | grep -q ":8080.*LISTEN" || ss -tln | grep -q ":8080.*LISTEN"; then
            # Web server is running - verify PID is still valid
            if ! kill -0 $WEB_SERVER_PID 2>/dev/null; then
                echo "ℹ Web server running but PID changed (normal for Python apps)" >&2
                # Update PID if possible
                NEW_PID=$(pgrep -f "uvicorn.*webserver" | head -1)
                if [ -n "$NEW_PID" ]; then
                    WEB_SERVER_PID=$NEW_PID
                    echo "✓ Updated web server PID to $WEB_SERVER_PID" >&2
                fi
            fi
        else
            echo "⚠ Web server not responding on port 8080, attempting restart..." >&2
            # Kill any lingering processes first
            pkill -f "uvicorn.*webserver" 2>/dev/null || true
            sleep 2
            start_webserver
        fi

        # Check crawler by PID AND by process
        if kill -0 $CRAWLER_PID 2>/dev/null || pgrep -f "mcp_engine" > /dev/null; then
            echo "✓ Crawler is running" >&2
        else
            echo "⚠ Crawler died, attempting restart..." >&2
            start_crawler
        fi

        # Status report (less verbose)
        if [ $(($(date +%s) % 300)) -eq 0 ]; then  # Every 5 minutes
            echo "Status: Web:$(netstat -tln 2>/dev/null | grep -q ":8080.*LISTEN" && echo "✓" || echo "✗") | Crawler:$(pgrep -f "mcp_engine" >/dev/null && echo "✓" || echo "✗") | Tor:$(pgrep tor >/dev/null && echo "✓" || echo "✗") | I2P:$(pgrep i2pd >/dev/null && echo "✓" || echo "✗")" >&2
        fi
    done
}

# Enhanced bootstrap function with working reseed URLs
bootstrap_i2p_via_tor() {
    echo "=== Bootstrapping I2P via Tor ===" >&2

    # Wait for Tor exit circuits
    if ! wait_for_tor_exit_circuits; then
        echo "⚠ Proceeding with I2P bootstrap despite Tor circuit issues" >&2
    fi

    # Test Tor connectivity
    echo "Testing Tor connectivity for I2P bootstrap..." >&2

    if ! netcat -z 127.0.0.1 9050 2>/dev/null; then
        echo "❌ Tor port 9050 not accessible" >&2
        return 1
    fi

    if ! timeout 15 curl -sSL --proxy socks5h://127.0.0.1:9050 --max-time 12 \
         "http://httpbin.org/ip" >/dev/null 2>&1; then
        echo "❌ Tor proxy not working" >&2
        return 1
    fi

    echo "✓ Tor connectivity confirmed" >&2

    # Create I2P netDb directories
    mkdir -p /var/lib/i2pd/netDb
    for prefix in {0..9} {a..f}; do
        for suffix in {0..9} {a..f}; do
            mkdir -p "/var/lib/i2pd/netDb/r${prefix}${suffix}"
        done
    done

    # Updated reseed URLs with correct paths - Based on current I2P infrastructure [1]
    local reseed_urls=(
        "https://reseed.diva.exchange/i2pseeds.su3"
        "https://i2p.novg.net/i2pseeds.su3"
        "https://reseed.memcpy.io/i2pseeds.su3"
        "https://reseed.i2p-projekt.de/i2pseeds.su3"
        "https://reseed.i2pgit.org/i2pseeds.su3"
        "https://banana.incognet.io/i2pseeds.su3"
        "https://reseed.onion.im/i2pseeds.su3"
        "https://i2pseed.creativecowpat.net:8443/i2pseeds.su3"
        "https://reseed.atomike.ninja/i2pseeds.su3"
        "http://193.150.121.66/netDb/routerInfo-"$(openssl rand -hex 16)".dat"
    )

    local success=false
    echo "Attempting to download I2P reseed data via Tor..." >&2

    for url in "${reseed_urls[@]}"; do
        echo "Trying to fetch reseed from $url via Tor..." >&2

        # Enhanced download with proper I2P headers and longer timeouts
        if timeout 90 curl -sSL --proxy socks5h://127.0.0.1:9050 \
           --max-time 85 --connect-timeout 25 --retry 1 \
           --user-agent "Wget/1.21.1" \
           -H "Accept: application/octet-stream, application/x-i2p, */*" \
           -H "Connection: close" \
           --fail --location \
           "$url" \
           -o "/tmp/i2pseeds.su3" 2>/dev/null; then

            # Verify the downloaded file
            if [ -f "/tmp/i2pseeds.su3" ] && [ -s "/tmp/i2pseeds.su3" ]; then
                local file_size=$(stat -c%s "/tmp/i2pseeds.su3" 2>/dev/null || echo "0")
                if [ "$file_size" -gt 5000 ]; then  # Reseed files should be substantial
                    echo "✓ Downloaded reseed data from $url ($file_size bytes)" >&2
                    success=true
                    break
                else
                    echo "✗ Downloaded file too small from $url ($file_size bytes)" >&2
                    rm -f "/tmp/i2pseeds.su3"
                fi
            else
                echo "✗ Failed to download or save from $url" >&2
            fi
        else
            echo "✗ Curl failed for $url" >&2
        fi

        # Delay between attempts
        sleep 3
    done

    if [ "$success" = true ]; then
        echo "✓ Reseed file downloaded successfully" >&2

        # Move the reseed file to a location where I2P can find it
        mkdir -p /var/lib/i2pd/reseed
        mv "/tmp/i2pseeds.su3" "/var/lib/i2pd/reseed/i2pseeds.su3" 2>/dev/null || true

        return 0
    else
        echo "❌ Failed to download any reseed data via Tor" >&2
        return 1
    fi
}

# Alternative: Create a minimal I2P setup
create_minimal_i2p_config() {
    echo "=== Creating Minimal I2P Configuration ===" >&2

    cat > /etc/i2pd/i2pd.conf << EOF
ipv4 = true
ipv6 = false
notransit = true
floodfill = false
nat = true

httpproxy.enabled = true
httpproxy.address = 0.0.0.0
httpproxy.port = 4444

http.enabled = true
http.address = 0.0.0.0
http.port = 7070

log = stdout
loglevel = error

# Very conservative reseed settings
reseed.verify = false
reseed.floodfill = false
reseed.threshold = 5
reseed.urls = https://reseed.i2p-projekt.de/

bandwidth = 128
share = 10

ntcp2.enabled = true
ntcp2.port = 0
ssu2.enabled = true
ssu2.port = 0

limits.transittunnels = 5
limits.openfiles = 2048

exploratory.inbound.length = 1
exploratory.outbound.length = 1
exploratory.inbound.quantity = 1
exploratory.outbound.quantity = 1

precomputation.elgamal = false
EOF

    echo "✓ Minimal I2P configuration created" >&2
}

# Updated force_i2p_reseed function
force_i2p_reseed() {
    echo "=== Enhanced I2P Bootstrap Strategy ===" >&2

    # Stop any existing I2P
    if [ -n "$I2P_PID" ] && kill -0 "$I2P_PID" 2>/dev/null; then
        echo "Stopping existing I2P..." >&2
        kill -TERM "$I2P_PID" 2>/dev/null || true
        sleep 3
        kill -KILL "$I2P_PID" 2>/dev/null || true
    fi

    # Clear broken netDb
    rm -rf /var/lib/i2pd/netDb/* 2>/dev/null || true
    create_i2p_directories

    # Strategy 1: Try bootstrap via Tor
    if bootstrap_i2p_via_tor; then
        echo "✓ I2P bootstrap via Tor successful" >&2
    else
        echo "⚠ Tor bootstrap failed, using minimal config" >&2
        create_minimal_i2p_config
    fi

    # Start I2P with extended timeout
    echo "Starting I2P with patient bootstrap..." >&2
    i2pd --conf=/etc/i2pd/i2pd.conf --datadir=/var/lib/i2pd 2>&1 &
    I2P_PID=$!

    # Give it more time - network connectivity seems limited
    echo "Waiting for I2P with extended patience (20 minutes)..." >&2
    wait_for_bootstrap 1200  # 20 minutes
}

# Function to check I2P health with environment variable respect
check_i2p_health() {
    # Use environment variables with sensible defaults
    local max_attempts=${I2P_HEALTH_CHECK_ATTEMPTS:-30}
    local check_interval=${I2P_HEALTH_CHECK_INTERVAL:-10}
    local expected_bytes=${I2P_EXPECT_CONSOLE_BYTES:-100}
    local initial_grace=${I2P_INITIAL_GRACE_MINUTES:-0}
    local attempt=1

    echo "Checking I2P health (attempts: $max_attempts, interval: ${check_interval}s, expected bytes: $expected_bytes)"

    # Initial grace period - don't check anything for X minutes
    if [ "$initial_grace" -gt 0 ]; then
        local grace_seconds=$((initial_grace * 60))
        echo "⏰ Initial grace period: ${initial_grace} minutes (${grace_seconds} seconds)"
        echo "   Not checking I2P during initial bootstrap time..."
        sleep $grace_seconds
        echo "✓ Grace period complete, starting health checks"
    fi

    while [ $attempt -le $max_attempts ]; do
        echo "Health check attempt $attempt/$max_attempts"

        # Check if process is running
        if ! pgrep -f "i2pd" > /dev/null; then
            echo "⚠ I2P process not running"
            return 1
        fi

        # Check if console port is responding with actual content
        local response_size=$(curl -s -m 5 http://0.0.0.0:7070 2>/dev/null | wc -c)

        if [ "$response_size" -gt "$expected_bytes" ]; then
            echo "✓ I2P console responding with $response_size bytes (expected >$expected_bytes)"

            # Check for error indicators in console
            local console_content=$(curl -s -m 5 http://0.0.0.0:7070 2>/dev/null)
            if echo "$console_content" | grep -qi "error\|fail\|problem"; then
                echo "⚠ I2P console shows errors"
                echo "Console content preview:"
                echo "$console_content" | head -10
            else
                echo "✓ I2P console appears healthy"
                return 0
            fi
        else
            echo "⚠ I2P console returning only $response_size bytes (expected >$expected_bytes)"
        fi

        sleep $check_interval
        attempt=$((attempt + 1))
    done

    echo "❌ I2P health check failed after $max_attempts attempts"
    return 1
}

# Function to restart I2P if it's broken
restart_i2p_if_broken() {
    echo "Attempting I2P restart..."

    # Kill any existing I2P processes
    pkill -f "i2pd" 2>/dev/null || true
    sleep 3

    # Clean up any lock files
    rm -f /var/lib/i2pd/i2pd.pid /var/lib/i2pd/*.pid /var/lib/i2pd/*.sock 2>/dev/null || true

    # Start I2P with verbose logging temporarily
    echo "Starting I2P with enhanced logging..."
    i2pd --conf=/etc/i2pd/i2pd.conf --datadir=/var/lib/i2pd --loglevel=info 2>&1 | \
        grep -v "RouterInfo: Can't save" | \
        grep -v "Profiling: No profile yet" &

    local new_i2p_pid=$!
    echo "✓ I2P restarted with PID: $new_i2p_pid"
    I2P_PID=$new_i2p_pid

    # Give it time to initialize
    sleep 10

    # Check if the restart worked
    if check_i2p_health; then
        echo "✓ I2P restart successful"
        return 0
    else
        echo "❌ I2P restart failed"
        return 1
    fi
}

# Instead of forcing reseed downloads, use minimal config approach
start_i2p_minimal() {
    echo "=== Starting I2P with Minimal Bootstrap ===" >&2

    # Create minimal I2P config that doesn't require immediate reseed
    cat > /etc/i2pd/i2pd.conf << EOF
ipv4 = true
ipv6 = false
notransit = true
floodfill = false
nat = true
datadir = /var/lib/i2pd

# HTTP Proxy
httpproxy.enabled = true
httpproxy.address = 0.0.0.0
httpproxy.port = 4444

# Console
http.enabled = true
http.address = 0.0.0.0
http.port = 7070

# Logging
log = stdout
loglevel = warn

# PATIENT reseed - let it find peers naturally
reseed.verify = false
reseed.threshold = 5
reseed.floodfill = false

# Minimal network settings
bandwidth = 128
share = 10

# Enable basic transport
ntcp2.enabled = true
ssu2.enabled = true

# Conservative limits
limits.transittunnels = 10
limits.openfiles = 1024
EOF

    # Start I2P and continue regardless of bootstrap status
    echo "Starting I2P with patient bootstrap..." >&2
    i2pd --conf=/etc/i2pd/i2pd.conf --daemon

    # Wait briefly for I2P to start, then continue
    sleep 30
    echo "✓ I2P started (will bootstrap in background)" >&2
}

# Replace your start_i2p function with this enhanced version
start_i2p_enhanced() {
    echo "=== Starting Enhanced I2P ==="

    # Check if we need forced reseed
    local existing_routes=0
    if [ -d "/var/lib/i2pd/netDb" ]; then
        existing_routes=$(find /var/lib/i2pd/netDb -name "*.dat" 2>/dev/null | wc -l)
    fi

    echo "Found $existing_routes existing routes"

    if [ "$existing_routes" -lt 5 ]; then
        echo "Insufficient routes - forcing bootstrap reseed"
        force_i2p_reseed
        return $?
    else
        echo "Using existing routes for faster startup"
        create_i2p_config

        # Normal startup
        echo "Starting I2P daemon..."
        i2pd --conf=/etc/i2pd/i2pd.conf --datadir=/var/lib/i2pd 2>&1 &
        I2P_PID=$!
        echo "✓ I2P started with PID: $I2P_PID"

        # Quick health check
        sleep 20
        check_i2p_health_enhanced
        return $?
    fi
}

# Enhanced health check
check_i2p_health_enhanced() {
    echo "=== Enhanced I2P Health Check ==="

    local max_attempts=30
    local attempt=1

    while [ $attempt -le $max_attempts ]; do
        echo "Health check attempt $attempt/$max_attempts"

        # Check process
        if ! kill -0 "$I2P_PID" 2>/dev/null; then
            echo "❌ I2P process not running"
            return 1
        fi

        # Check console
        local console_response
        console_response=$(curl -s --max-time 5 "http://0.0.0.0:7070" 2>/dev/null | wc -c)

        if [ "$console_response" -gt 100 ]; then
            echo "✓ I2P console healthy ($console_response bytes)"

            # Check routes
            local route_count=0
            if [ -d "/var/lib/i2pd/netDb" ]; then
                route_count=$(find /var/lib/i2pd/netDb -name "*.dat" 2>/dev/null | wc -l)
            fi

            if [ "$route_count" -ge 5 ]; then
                echo "✓ I2P network ready ($route_count routes)"
                return 0
            else
                echo "⚠ Waiting for more routes ($route_count found)..."
            fi
        else
            echo "⚠ I2P console responding with only $console_response bytes"
        fi

        sleep 10
        attempt=$((attempt + 1))
    done

    echo "❌ I2P health check failed after $max_attempts attempts"
    return 1
}

# Add this coordination section near the end of entrypoint.sh
coordinate_startup() {
    echo "=== Coordinating Service Startup ==="

    # Start services in proper order
    echo "Step 1: Starting Tor..."
    start_tor &

    echo "Step 2: Starting I2P..."
    start_i2p_minimal

    echo "Step 3: Creating startup signal for Python app..."
    # Create a signal file that Python can check
    touch /tmp/proxies_ready

    # Export I2P PID for Python to detect
    echo "$I2P_PID" > /tmp/i2p.pid

    echo "Step 4: Starting web server..."
    start_webserver

    echo "Step 5: Starting Python crawler..."
    start_crawler

    echo "✓ All services coordinated"
}


main() {
    echo "=== DeepWeb Proxy Starting ==="

    # System setup
    set_enhanced_system_limits || exit 1
    create_directories
    setup_ssl_certificates

    # Coordinate startup (new approach)
    coordinate_startup

    # Setup signal handlers for graceful shutdown
    trap 'echo "Shutdown signal received"; cleanup_processes; exit 0' SIGTERM SIGINT

    # Main monitoring loop
    echo "=== Main Process Started ==="
    echo "All services started. Entering monitoring mode..."

    # Keep container running and monitor services
    while true; do
        # Check if any critical process died and restart if needed
        if ! kill -0 $TOR_PID 2>/dev/null; then
            echo "❌ Tor process died, restarting..."
            tor -f /etc/tor/torrc &
            TOR_PID=$!
        fi

        if ! kill -0 $I2P_PID 2>/dev/null; then
            echo "❌ I2P process died, restarting..."
            i2pd --conf=/etc/i2pd/i2pd.conf --datadir=/var/lib/i2pd 2>&1 &
            I2P_PID=$!
        fi

        if ! kill -0 $WEBSERVER_PID 2>/dev/null; then
            echo "❌ Web server died, restarting..."
            cd /app
            python3 webserver.py &
            WEBSERVER_PID=$!
        fi

        if ! kill -0 $MCP_PID 2>/dev/null; then
            echo "❌ MCP crawler died, restarting..."
            cd /app
            #Create log file
            MCP_LOG_FILE="/mnt/config/mcp_engine.log"
            echo "MCP Engine output will be saved to: $MCP_LOG_FILE"

            # Start MCP engine with tee to output to both console and file
            # Use exec to prevent subshell issues with PID tracking
            python3 mcp_engine.py 2>&1 | tee "$MCP_LOG_FILE" &
            MCP_PID=$!
            echo "MCP crawler started with PID: $MCP_PID"
            echo "Log file: $MCP_LOG_FILE"
        fi

        # Status check every 5 minutes
        sleep 300
        echo "Services status check at $(date):"
        echo "  Tor: $(kill -0 $TOR_PID 2>/dev/null && echo '✓' || echo '✗')"
        echo "  I2P: $(kill -0 $I2P_PID 2>/dev/null && echo '✓' || echo '✗')"
        echo "  Web: $(kill -0 $WEBSERVER_PID 2>/dev/null && echo '✓' || echo '✗')"
        echo "  MCP: $(kill -0 $MCP_PID 2>/dev/null && echo '✓' || echo '✗')"
    done
}

# Call main function - THIS REPLACES whatever is currently at the bottom
main "$@"

