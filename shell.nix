{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = with pkgs; [
    python3
    python3Packages.requests
    python3Packages.beautifulsoup4
    python3Packages.fastapi
    python3Packages.uvicorn
    python3Packages.pip
    python3Packages.pymysql
    python3Packages.sqlalchemy
    python3Packages.cryptography
    python3Packages.python-dateutil
    python3Packages.pillow
    python3Packages.urllib3
    python3Packages.pysocks  # For SOCKS proxy support
    file  # For mime type detection
    tor
    i2pd
    socat  # For debugging network connections
  ];

  shellHook = ''
    # Start Tor if not already running
    if ! pgrep -x tor > /dev/null; then
      echo "Starting Tor service for development..."
      tor --RunAsDaemon 1
    fi

    # Start I2P if not already running
    if ! pgrep -x i2pd > /dev/null; then
      echo "Starting I2P service for development..."
      i2pd --daemon
    fi

    echo "Development environment ready!"
    echo "Tor SOCKS proxy should be available at 127.0.0.1:9050"
    echo "I2P proxy should be available at 127.0.0.1:4444"
  '';
}