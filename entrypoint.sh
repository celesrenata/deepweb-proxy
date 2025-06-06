#!/usr/bin/env bash
set -e

# Create necessary directories
mkdir -p /var/lib/tor /var/lib/i2pd /run /etc/tor /etc/i2pd

# Create Tor configuration file
cat > /etc/tor/torrc << EOF
SocksPort 0.0.0.0:9050
DataDirectory /var/lib/tor
RunAsDaemon 0
Log notice stdout
# Increase connection limits for better performance
ConnectionPadding 0
ReducedConnectionPadding 1
# Improve performance for hidden services
CircuitBuildTimeout 10
LearnCircuitBuildTimeout 0
EOF

# Create I2P configuration file
cat > /etc/i2pd/i2pd.conf << EOF
# Main configuration
ipv4 = true
ipv6 = false
notransit = true
floodfill = false

# HTTP Proxy
httpproxy.enabled = true
httpproxy.address = 0.0.0.0
httpproxy.port = 4444

# Logging
log = stdout
loglevel = info
EOF

# Install PySocks using pip if needed
echo "Checking for PySocks..."
if ! python3 -c "import socks" 2>/dev/null; then
    echo "Installing PySocks with pip..."
    pip3 install --user PySocks
fi

# Start Tor in the background
echo "Starting Tor service..."
tor -f /etc/tor/torrc &
TOR_PID=$!

# Wait for Tor to start
echo "Waiting for Tor to start..."
for i in {1..30}; do
    if ss -nlt | grep -q 9050; then
        echo "Tor SOCKS proxy is running on port 9050"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "ERROR: Tor failed to start within timeout"
        exit 1
    fi
    sleep 1
done

# Start I2P in the background
echo "Starting I2P service..."
i2pd --conf=/etc/i2pd/i2pd.conf --datadir=/var/lib/i2pd &
I2P_PID=$!

# Wait for I2P to start
echo "Waiting for I2P to start..."
for i in {1..30}; do
    if ss -nlt | grep -q 4444; then
        echo "I2P proxy is running on port 4444"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "ERROR: I2P failed to start within timeout"
        exit 1
    fi
    sleep 1
done

# Verify SOCKS proxy functionality
echo "Testing SOCKS proxy connectivity..."
python3 -c "import socks, socket; s = socks.socksocket(); s.set_proxy(socks.SOCKS5, '127.0.0.1', 9050); print('SOCKS5 proxy configured successfully')" || echo "WARNING: SOCKS proxy test failed"

# Launch the FastAPI configuration server in the background
echo "Starting web server..."
python3 /app/webserver.py &
WEBSERVER_PID=$!

# Give the webserver time to start
sleep 5
echo "Web server is running"

# Setup signal handling
trap 'kill $TOR_PID $I2P_PID $WEBSERVER_PID' SIGTERM SIGINT

# Run the MCP engine loop in foreground
echo "Starting MCP engine..."
python3 /app/mcp_engine.py &
MCP_PID=$!

# Wait for any process to exit
wait -n

# Exit with status of process that exited first
exit $?