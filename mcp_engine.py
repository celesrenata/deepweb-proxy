import concurrent
import math
import os
import time
import logging
import requests
import asyncio
import aiohttp
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from db_models import get_db_session, Site, Page, MediaFile
from queue import Queue
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, LargeBinary, Float, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.exc import IntegrityError
import socks
import socket
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import mimetypes
from minio import Minio
from minio.error import S3Error
import hashlib
from PIL import Image
import io
import json
import threading


# I2P Internal Proxy Services Configuration
I2P_INTERNAL_PROXIES_STR = os.getenv("I2P_INTERNAL_PROXIES", "")
I2P_PROXY_FALLBACK_MODE = os.getenv("I2P_PROXY_FALLBACK_MODE", "failover")  # failover, round_robin, random

# Parse I2P internal proxy services - simple list approach
I2P_INTERNAL_PROXIES = []
if I2P_INTERNAL_PROXIES_STR:
    for p in I2P_INTERNAL_PROXIES_STR.split(','):
        proxy = p.strip()
        if proxy:
            I2P_INTERNAL_PROXIES.append(proxy)
else:
    # Default I2P proxy services
    I2P_INTERNAL_PROXIES = ['notbob.i2p', 'purokishi.i2p', 'false.i2p', 'stormycloud.i2p']

# I2P Internal Proxy Service Configuration - Dictionary format for detailed info
I2P_PROXY_SERVICES = {
    'notbob.i2p': {
        'description': 'NotBob I2P outproxy service',
        'endpoint': 'http://notbob.i2p',
        'type': 'outproxy',
        'reliability': 'high'
    },
    'purokishi.i2p': {
        'description': 'Purokishi outproxy service',
        'endpoint': 'http://outproxy.purokishi.i2p',
        'type': 'outproxy',
        'reliability': 'medium'
    },
    'stormycloud.i2p': {
        'description': 'StormyCloud exit service',
        'endpoint': 'http://exit.stormycloud.i2p',
        'type': 'outproxy',
        'reliability': 'medium'
    },
    'meeh.i2p': {
        'description': 'Meeh Tor-I2P bridge',
        'endpoint': 'http://outproxy-tor.meeh.i2p',
        'type': 'bridge',
        'reliability': 'low'
    },
    'false.i2p': {
        'description': 'False outproxy (no external access)',
        'endpoint': 'http://false.i2p',
        'type': 'internal_only',
        'reliability': 'high'
    }
}

def setup_logging():
    """Setup comprehensive logging configuration"""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    logging.basicConfig(
        level=getattr(logging, log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler()
        ]
    )

    # Set specific log levels for noisy libraries
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('PIL').setLevel(logging.WARNING)

    # Create logger
    logger = logging.getLogger(__name__)

    return logger


def setup_ssl_for_python():
    """Setup SSL configuration for Python requests"""
    import ssl
    import os

    # Use the same certificate path that works with curl
    cert_file = os.environ.get('SSL_CERT_FILE') or os.environ.get('CURL_CA_BUNDLE')

    if cert_file and os.path.isfile(cert_file):
        # Set for requests library
        os.environ['REQUESTS_CA_BUNDLE'] = cert_file
        logger.info(f"✓ Python SSL configured with: {cert_file}")
        return True
    else:
        logger.warning("⚠ No valid SSL certificate file found for Python")
        return False


# Initialize logger
logger = setup_logging()

# Sites and configuration
SITES_FILE = os.getenv("SITES_FILE", "/mnt/config/sites.txt")

# Parallel processing configuration
PARALLEL_SITES = int(os.getenv("PARALLEL_SITES", "3"))  # Reduced for DB stability
PARALLEL_PAGES = int(os.getenv("PARALLEL_PAGES", "2"))  # Reduced for DB stability

# AI Models Configuration
OLLAMA_ENDPOINT = os.getenv("OLLAMA_API_URL", "http://10.1.1.12:2701")
RESEARCH_MODEL = os.getenv("OLLAMA_MODEL", "gemma")
ENTITY_MODEL = os.getenv("ENTITY_MODEL", "gemma")
SENTIMENT_MODEL = os.getenv("SENTIMENT_MODEL", "gemma")
MULTIMODAL_MODEL = os.getenv("MULTIMODAL_MODEL", "gemma")

# Proxy Configuration
TOR_SOCKS_PORT = int(os.getenv("TOR_SOCKS_PORT", "9050"))
I2P_HTTP_PROXY_PORT = int(os.getenv("I2P_HTTP_PROXY_PORT", "4444"))

# Feature toggles
ENABLE_TOR = os.getenv("ENABLE_TOR", "true").lower() == "true"
ENABLE_I2P = os.getenv("ENABLE_I2P", "true").lower() == "true"

# I2P Internal Services Configuration (corrected terminology)
USE_I2P_INTERNAL_PROXIES = os.getenv("USE_I2P_INTERNAL_PROXIES", "false").lower() == "true"
I2P_INTERNAL_PROXIES_STR = os.getenv("I2P_INTERNAL_PROXIES", "")
I2P_PROXY_FALLBACK_MODE = os.getenv("I2P_PROXY_FALLBACK_MODE", "local")  # local, internal_services, hybrid

# External Tor Proxy Configuration
USE_EXTERNAL_TOR_PROXIES = os.getenv("USE_EXTERNAL_TOR_PROXIES", "false").lower() == "true"
EXTERNAL_TOR_PROXIES_STR = os.getenv("EXTERNAL_TOR_PROXIES", "")
TOR_PROXY_FALLBACK_MODE = os.getenv("TOR_PROXY_FALLBACK_MODE", "local")  # local, external, hybrid

# Parse proxy lists
I2P_INTERNAL_PROXIES = [p.strip() for p in I2P_INTERNAL_PROXIES_STR.split(",") if p.strip()] if I2P_INTERNAL_PROXIES_STR else []
EXTERNAL_TOR_PROXIES = [p.strip() for p in EXTERNAL_TOR_PROXIES_STR.split(",") if p.strip()] if EXTERNAL_TOR_PROXIES_STR else []

# Research Configuration
MAX_RESEARCH_DEPTH = int(os.getenv("MAX_RESEARCH_DEPTH", "3"))
ANALYSIS_BATCH_SIZE = int(os.getenv("ANALYSIS_BATCH_SIZE", "10"))
RESEARCH_FREQUENCY_HOURS = int(os.getenv("CRAWL_FREQUENCY_HOURS", "24"))

# Crawling Configuration
CRAWL_DEPTH = int(os.getenv("CRAWL_DEPTH", "3"))
MAX_PAGES_PER_SITE = int(os.getenv("MAX_PAGES_PER_SITE", "500"))

# Media Configuration
MAX_IMAGE_SIZE = int(os.getenv("MAX_IMAGE_SIZE", "10485760"))  # 10MB
MAX_AUDIO_SIZE = int(os.getenv("MAX_AUDIO_SIZE", "10485760"))  # 10MB
MAX_VIDEO_SIZE = int(os.getenv("MAX_VIDEO_SIZE", "52428800"))  # 50MB
MAX_MEDIA_PER_PAGE = int(os.getenv("MAX_MEDIA_PER_PAGE", "0"))  # 0 = unlimited
DOWNLOAD_ALL_MEDIA = os.getenv("DOWNLOAD_ALL_MEDIA", "true").lower() == "true"

# MinIO Configuration
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio-crawler-hl.minio-service:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"
MINIO_BUCKET_IMAGES = os.getenv("MINIO_BUCKET_IMAGES", "crawler-images")
MINIO_BUCKET_AUDIO = os.getenv("MINIO_BUCKET_AUDIO", "crawler-audio")
MINIO_BUCKET_VIDEO = os.getenv("MINIO_BUCKET_VIDEO", "crawler-videos")
MINIO_BUCKET_OTHER = os.getenv("MINIO_BUCKET_OTHER", "crawler-media")

# Database Configuration - using deployment environment variables
DB_HOST = os.getenv("MYSQL_HOST", "mariadb.mariadb-service")
DB_PORT = os.getenv("MYSQL_PORT", "3306")
DB_USER = os.getenv("MYSQL_USER", "splinter-research")
DB_PASS = os.getenv("MYSQL_PASSWORD", "")
DB_NAME = os.getenv("MYSQL_DATABASE", "splinter-research")

# Enhanced database URL with connection pooling
DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"

# Create engine with better connection pooling for parallel processing
engine = create_engine(
    DATABASE_URL,
    pool_size=20,  # Increased pool size for parallel processing
    max_overflow=30,  # Allow extra connections
    pool_timeout=30,  # Wait up to 30 seconds for a connection
    pool_recycle=3600,  # Recycle connections every hour
    pool_pre_ping=True,  # Validate connections before use
    echo=False  # Set to True for SQL debugging
)

# Create thread-safe session factory
SessionLocal = scoped_session(sessionmaker(bind=engine))

# I2P Proxy Services Configuration
I2P_PROXY_SERVICES = [
    {"host": "0.0.0.0", "port": I2P_HTTP_PROXY_PORT, "type": "http"}
]


class AIResearchCrawler:
    def __init__(self):
        """Initialize with gentle I2P management"""
        self.executor = ThreadPoolExecutor(max_workers=min(4, (os.cpu_count() or 1) + 1))
        logger.info(f"✓ Thread pool executor initialized with {self.executor._max_workers} workers")

        # Initialize thread-local storage
        self.local = threading.local()

        # Initialize database session
        if not self._init_database_session():
            logger.error("Failed to initialize database session")
            raise RuntimeError("Database initialization failed")

        # Setup database schema
        if not self._setup_database_schema():
            logger.error("Failed to setup database schema")
            raise RuntimeError("Database schema setup failed")

        # Setup MinIO client
        self._setup_minio_client()

        # Test MinIO connection
        self.test_minio_connection()

        # Setup proxy sessions
        self._setup_proxy_sessions()

        # Setup SSL for Python
        setup_ssl_for_python()

        # Use gentle I2P management
        logger.info("=== Gentle I2P Initialization ===")

        # First check if I2P is already working
        if self._gentle_i2p_health_check():
            logger.info("✓ I2P already working")
            self.i2p_working = True
        else:
            logger.info("I2P needs time to initialize...")

            # Be patient first
            if self._patient_i2p_wait(max_minutes=8):
                logger.info("✓ I2P ready after patient wait")
                self.i2p_working = True
            else:
                # Only restart if truly needed
                if self._only_restart_if_truly_broken():
                    logger.info("✓ I2P working after gentle intervention")
                    self.i2p_working = True
                else:
                    logger.warning("I2P not ready - will continue with Tor only")
                    self.i2p_working = False

        # Test proxies gently
        tor_ok, i2p_ok = self._gentle_proxy_test()
        logger.info(f"Proxy status: Tor={'✓' if tor_ok else '✗'}, I2P={'✓' if i2p_ok else '✗'}")

    def _get_thread_session(self):
        """Get a thread-local database session"""
        if not hasattr(self.local, 'session'):
            self.local.session = SessionLocal()
        return self.local.session

    def _init_database_session(self):
        """Initialize main database session"""
        try:
            # Test database connection
            self.session = SessionLocal()
            self.session.execute(text("SELECT 1")).fetchone()
            logger.info("✓ Database connection established successfully")
            return True
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            return False

    def _setup_database_schema(self):
        """Setup database schema with enhanced research capabilities"""
        try:
            from db_models import Base, engine, init_db

            # Initialize database schema
            if init_db():
                logger.info("✓ Database schema setup completed")
                return True
            else:
                logger.error("Failed to initialize database schema")
                return False

        except Exception as e:
            logger.error(f"Error setting up database schema: {e}")
            return False

    def _setup_minio_client(self):
        """Setup MinIO client for media storage"""
        try:
            if not MINIO_ACCESS_KEY or not MINIO_SECRET_KEY:
                logger.warning("MinIO credentials not provided - falling back to database storage")
                return False

            # Create MinIO client
            self.minio_client = Minio(
                MINIO_ENDPOINT,
                access_key=MINIO_ACCESS_KEY,
                secret_key=MINIO_SECRET_KEY,
                secure=MINIO_SECURE
            )

            # Test connection
            try:
                self.minio_client.list_buckets()
                logger.info("✓ MinIO connection successful")
            except Exception as e:
                logger.warning(f"MinIO connection test failed: {e}")
                return False

            # Create buckets if they don't exist
            buckets = [MINIO_BUCKET_IMAGES, MINIO_BUCKET_AUDIO, MINIO_BUCKET_VIDEO, MINIO_BUCKET_OTHER]
            for bucket in buckets:
                try:
                    if not self.minio_client.bucket_exists(bucket):
                        self.minio_client.make_bucket(bucket)
                        logger.info(f"Created MinIO bucket: {bucket}")
                except Exception as e:
                    logger.error(f"Error creating bucket {bucket}: {e}")
                    return False

            logger.info("✓ MinIO client setup completed")
            return True

        except Exception as e:
            logger.error(f"Error setting up MinIO client: {e}")
            return False

    def _setup_proxy_sessions(self):
        """Enhanced proxy setup - ALL traffic through proxies"""
        logger.info("Setting up proxy sessions (Tor-only mode for clearnet)...")

        # DO NOT SET UP clearnet_session - we want all traffic through Tor
        # Remove this line: self.clearnet_session = requests.Session()

        # Set up Tor session with connection pooling - MANDATORY for all clearnet
        if ENABLE_TOR:
            self.tor_session = requests.Session()
            self.tor_session.proxies = {
                'http': f'socks5h://127.0.0.1:{TOR_SOCKS_PORT}',
                'https': f'socks5h://127.0.0.1:{TOR_SOCKS_PORT}'
            }
            # Add retry strategy
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry

            retry_strategy = Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
            )
            adapter = HTTPAdapter(max_retries=retry_strategy)
            self.tor_session.mount("http://", adapter)
            self.tor_session.mount("https://", adapter)

            logger.info("✓ Tor session configured for ALL clearnet traffic")
        else:
            logger.error("❌ CRITICAL: Tor is disabled but required for all clearnet traffic")
            raise RuntimeError("Tor must be enabled for privacy compliance")

        # Set up I2P session with timeout handling
        if ENABLE_I2P:
            self.i2p_session = requests.Session()
            self.i2p_session.proxies = {
                'http': f'http://0.0.0.0:{I2P_HTTP_PROXY_PORT}',
                'https': f'http://0.0.0.0:{I2P_HTTP_PROXY_PORT}'
            }

            # Test I2P connectivity with patience
            self.i2p_working = self._test_i2p_with_patience()

            if not self.i2p_working:
                logger.warning("I2P not working - will use Tor fallback for .i2p sites")

            # Setup I2P internal proxy sessions
            self._setup_i2p_internal_proxies()

        # Setup external proxy sessions if configured
        self._setup_external_proxies()

        logger.info("✓ Proxy sessions configured - ALL clearnet traffic routes through Tor")

    def _setup_i2p_internal_proxies(self):
        """Setup I2P internal proxy services with proper error handling"""
        if not USE_I2P_INTERNAL_PROXIES:
            logger.info("I2P internal proxies disabled")
            return

        logger.info("=== Setting up I2P Internal Proxy Services ===")

        # Initialize containers
        self.i2p_sessions = {}
        self.i2p_proxy_status = {}

        # Get the list of I2P proxy services
        proxy_list = I2P_INTERNAL_PROXIES if I2P_INTERNAL_PROXIES else []
        logger.info(f"Using configured I2P proxies: {proxy_list}")

        # Setup each proxy service
        for proxy_service in proxy_list:
            try:
                # Clean the proxy service name
                proxy_service = proxy_service.strip()
                if not proxy_service:
                    continue

                logger.info(f"Setting up I2P proxy: {proxy_service}")

                # Create session for this proxy
                session = requests.Session()

                # Configure the session to use I2P HTTP proxy
                session.proxies = {
                    'http': f'http://127.0.0.1:{I2P_HTTP_PROXY_PORT}',
                    'https': f'http://127.0.0.1:{I2P_HTTP_PROXY_PORT}'
                }

                # Set reasonable timeouts
                session.timeout = 30

                # Add headers to identify as coming through I2P
                session.headers.update({
                    'User-Agent': f'I2P-Proxy-Client/{proxy_service}',
                    'X-I2P-Proxy': proxy_service
                })

                # Store the session
                self.i2p_sessions[proxy_service] = session
                self.i2p_proxy_status[proxy_service] = {
                    'status': 'initialized',
                    'last_test': None,
                    'working': False,
                    'error_count': 0
                }

                logger.info(f"✓ I2P proxy {proxy_service} session created")

            except Exception as e:
                logger.error(f"❌ Failed to setup I2P proxy {proxy_service}: {e}")
                # Continue with other proxies
                continue

        logger.info(f"✓ I2P internal proxy setup complete - {len(self.i2p_sessions)} proxies configured")

        # Test the proxies if any were configured
        if self.i2p_sessions:
            logger.info("Testing I2P proxy services...")
            try:
                self.test_i2p_proxy_services()
            except Exception as e:
                logger.warning(f"I2P proxy testing failed: {e}")
        else:
            logger.warning("No I2P proxy services were successfully configured")

    def _setup_external_proxies(self):
        """Setup external proxy sessions (Tor and others)"""
        self.tor_sessions = {}

        if USE_EXTERNAL_TOR_PROXIES and EXTERNAL_TOR_PROXIES:
            logger.info("Setting up external Tor proxy services...")

            for proxy_url in EXTERNAL_TOR_PROXIES:
                session = requests.Session()
                session.proxies = {
                    'http': proxy_url,
                    'https': proxy_url
                }
                self.tor_sessions[proxy_url] = session
                logger.debug(f"Configured external Tor proxy: {proxy_url}")

        logger.debug("External proxy setup completed")

    def _test_i2p_with_patience(self, max_attempts=30):
        """Test I2P connectivity with multiple attempts"""
        logger.info("Testing I2P connectivity with patience...")

        test_urls = [
            "http://httpbin.org/ip",  # Via outproxy
            "http://stats.i2p/",  # Internal I2P site
        ]

        for attempt in range(max_attempts):
            for test_url in test_urls:
                try:
                    logger.info(f"I2P test attempt {attempt + 1}/{max_attempts}: {test_url}")

                    response = self.i2p_session.get(
                        test_url,
                        timeout=20,  # Longer timeout for I2P
                        allow_redirects=True
                    )

                    if response.status_code == 200:
                        logger.info(f"✓ I2P working via {test_url}")
                        return True

                except Exception as e:
                    logger.debug(f"I2P test failed: {e}")

            if attempt < max_attempts - 1:
                logger.info(f"I2P test failed, waiting 30s before retry...")
                time.sleep(30)

        return False

    def test_i2p_proxy_services(self):
        """Test each I2P proxy service independently"""
        if not self.i2p_sessions:
            logger.warning("No I2P proxy sessions to test")
            return False

        logger.info("=== Testing I2P Proxy Services ===")

        # Test URLs - mix of I2P internal and external (via outproxy)
        test_scenarios = [
            {
                'name': 'I2P Internal',
                'urls': ['http://stats.i2p/', 'http://i2p-projekt.i2p/'],
                'timeout': 20
            },
            {
                'name': 'External via Outproxy',
                'urls': ['http://httpbin.org/ip', 'http://example.com/'],
                'timeout': 30
            }
        ]

        working_proxies = []

        for proxy_name, session in self.i2p_sessions.items():
            proxy_working = False
            proxy_config = self.i2p_proxy_status[proxy_name]['config']

            logger.info(f"Testing {proxy_name} ({proxy_config['description']})...")

            # Test appropriate scenarios based on proxy type
            test_external = proxy_config['type'] in ['outproxy', 'bridge']

            for scenario in test_scenarios:
                # Skip external tests for internal-only proxies
                if scenario['name'] == 'External via Outproxy' and not test_external:
                    continue

                for test_url in scenario['urls']:
                    try:
                        logger.debug(f"  Testing {proxy_name} with {test_url}")

                        # For external URLs via outproxy, we need to configure the outproxy
                        if scenario['name'] == 'External via Outproxy':
                            # Temporarily set outproxy header
                            original_headers = session.headers.copy()
                            session.headers['X-I2P-Outproxy'] = proxy_config['endpoint']

                        response = session.get(test_url, timeout=scenario['timeout'])

                        if response.status_code == 200:
                            logger.info(f"  ✓ {proxy_name} working with {test_url}")
                            proxy_working = True
                            self.i2p_proxy_status[proxy_name]['success_count'] += 1
                            break  # Success with this proxy

                        # Restore headers if modified
                        if scenario['name'] == 'External via Outproxy':
                            session.headers = original_headers

                    except Exception as e:
                        logger.debug(f"  ❌ {proxy_name} failed with {test_url}: {str(e)[:100]}")
                        self.i2p_proxy_status[proxy_name]['error_count'] += 1
                        continue

                if proxy_working:
                    break  # No need to test more scenarios for this proxy

            # Update proxy status
            self.i2p_proxy_status[proxy_name]['working'] = proxy_working
            self.i2p_proxy_status[proxy_name]['last_tested'] = time.time()

            if proxy_working:
                working_proxies.append(proxy_name)
                logger.info(f"  ✓ {proxy_name} is functional")
            else:
                logger.warning(f"  ❌ {proxy_name} not responding")

        if working_proxies:
            logger.info(f"✓ Working I2P proxies: {', '.join(working_proxies)}")
            return True
        else:
            logger.warning("❌ No I2P proxy services are currently working")
            return False

    def _test_proxy_connectivity(self):
        """Test all proxy connectivity including I2P internal services"""
        logger.info("=== Comprehensive Proxy Testing ===")

        tor_working = False
        i2p_working = False

        # Test Tor
        if hasattr(self, 'tor_session') and self.tor_session:
            logger.info("Testing Tor proxy...")
            try:
                response = self.tor_session.get(
                    "http://httpbin.org/ip",
                    timeout=15
                )
                if response.status_code == 200:
                    logger.info("✓ Tor proxy working")
                    tor_working = True
                else:
                    logger.info(f"Tor proxy returned status {response.status_code}")
            except Exception as e:
                logger.info(f"Tor proxy not ready: {str(e)[:100]}")

        # Test standard I2P
        if hasattr(self, 'i2p_session') and self.i2p_session:
            logger.info("Testing standard I2P proxy...")
            try:
                response = self.i2p_session.get(
                    "http://stats.i2p/",
                    timeout=25
                )
                if response.status_code == 200:
                    logger.info("✓ Standard I2P proxy working")
                    i2p_working = True
                else:
                    logger.info(f"Standard I2P proxy returned status {response.status_code}")
            except Exception as e:
                logger.info(f"Standard I2P proxy not ready: {str(e)[:100]}")

        # Test I2P internal proxy services
        if USE_I2P_INTERNAL_PROXIES:
            logger.info("Testing I2P internal proxy services...")
            internal_working = self.test_i2p_proxy_services()
            if internal_working:
                i2p_working = True  # At least one I2P method is working

        # Check if we should continue based on proxy status
        if not tor_working and not i2p_working:
            logger.warning("❌ CRITICAL: Both Tor and I2P proxies failed - cannot access .onion or .i2p sites")
            logger.warning("Crawler will wait for proxy services to become available...")
            return False, False

        return tor_working, i2p_working

    def get_appropriate_session(self, url):
        """Get the appropriate session for a URL - ALL clearnet traffic goes through Tor"""
        logger.info(f"🔍 Selecting session for URL: {url}")

        # Parse URL to determine type
        parsed = urlparse(url)
        domain = parsed.hostname.lower() if parsed.hostname else ""

        # Handle .onion domains - use Tor
        if domain.endswith('.onion'):
            if ENABLE_TOR and hasattr(self, 'tor_session'):
                logger.info("🧅 Using Tor session for .onion URL")
                return self.tor_session, "tor"
            else:
                logger.error("❌ Tor required for .onion but not available")
                return None, None

        # Handle .i2p domains - use I2P with fallback to Tor
        elif domain.endswith('.i2p'):
            if ENABLE_I2P and hasattr(self, 'i2p_session') and self.i2p_working:
                logger.info("🌐 Using I2P session for .i2p URL")
                return self.i2p_session, "i2p"
            elif ENABLE_TOR and hasattr(self, 'tor_session'):
                logger.info("🌐 Using Tor session for .i2p URL (I2P fallback)")
                return self.tor_session, "tor"
            else:
                logger.error("❌ No proxy available for .i2p URL")
                return None, None

        # ALL OTHER DOMAINS (clearnet) - MUST go through Tor
        else:
            if ENABLE_TOR and hasattr(self, 'tor_session'):
                logger.info("🔒 Using Tor session for clearnet URL (privacy mode)")
                return self.tor_session, "tor"
            else:
                logger.error("❌ Tor required for all clearnet traffic but not available")
                return None, None

    def get_best_i2p_session(self, url=None):
        """Get the best I2P session based on proxy status and fallback mode"""
        if not hasattr(self, 'i2p_sessions') or not self.i2p_sessions:
            logger.warning("No I2P sessions available")
            return None

        # Filter working proxies
        working_proxies = []
        if hasattr(self, 'i2p_proxy_status'):
            working_proxies = [
                name for name, status in self.i2p_proxy_status.items()
                if status.get('working', False)
            ]

        if not working_proxies:
            # Fallback to any available session
            working_proxies = list(self.i2p_sessions.keys())
            logger.warning(f"No confirmed working I2P proxies, using any available: {working_proxies}")

        if not working_proxies:
            logger.error("No I2P proxies available at all")
            return None

        # Apply fallback mode strategy
        if I2P_PROXY_FALLBACK_MODE == 'round_robin':
            # Simple round-robin (could be improved with persistent state)
            selected = working_proxies[int(time.time()) % len(working_proxies)]
        elif I2P_PROXY_FALLBACK_MODE == 'random':
            import random
            selected = random.choice(working_proxies)
        else:  # failover mode (default)
            # Use the first working proxy (by reliability order)
            selected = working_proxies[0]

        logger.debug(f"Selected I2P proxy: {selected} (mode: {I2P_PROXY_FALLBACK_MODE})")
        return self.i2p_sessions.get(selected)

    def get_best_i2p_session(self, url=None):
        """Get the best I2P session based on proxy status and fallback mode"""
        if not self.i2p_sessions:
            return None

        # Filter working proxies
        working_proxies = [
            name for name, status in self.i2p_proxy_status.items()
            if status.get('working', False)
        ]

        if not working_proxies:
            # Fallback to any available session
            working_proxies = list(self.i2p_sessions.keys())
            logger.warning(f"No confirmed working I2P proxies, using any available: {working_proxies}")

        if not working_proxies:
            return None

        # Apply fallback mode strategy
        if I2P_PROXY_FALLBACK_MODE == 'round_robin':
            # Simple round-robin (could be improved with persistent state)
            selected = working_proxies[int(time.time()) % len(working_proxies)]
        elif I2P_PROXY_FALLBACK_MODE == 'random':
            import random
            selected = random.choice(working_proxies)
        else:  # failover mode (default)
            # Use the first working proxy (by reliability order)
            selected = working_proxies[0]

        logger.debug(f"Selected I2P proxy: {selected} (mode: {I2P_PROXY_FALLBACK_MODE})")
        return self.i2p_sessions[selected]

    def get_or_create_site(self, url, session=None):
        """Get or create a Site object with proper session handling."""
        if session is None:
            session = self._get_thread_session()

        try:
            # Parse URL to determine type
            parsed = urlparse(url)
            domain = parsed.netloc.lower()

            is_onion = domain.endswith('.onion')
            is_i2p = domain.endswith('.i2p')

            # Check if site already exists in this session
            site = session.query(Site).filter_by(url=url).first()

            if not site:
                # Create new site
                site = Site(
                    url=url,
                    is_onion=is_onion,
                    is_i2p=is_i2p
                )
                session.add(site)
                session.commit()
                logger.info(f"➕ Created new site: {url}")
            else:
                logger.info(f"🔄 Using existing site: {url}")

            return site

        except Exception as e:
            logger.error(f"❌ Error creating/getting site {url}: {e}")
            session.rollback()
            raise

    def read_sites(self):
        """Read sites from configuration file"""
        try:
            if not os.path.exists(SITES_FILE):
                logger.warning(f"Sites file not found: {SITES_FILE}")
                return []

            sites = []
            with open(SITES_FILE, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        sites.append(line)

            logger.info(f"Loaded {len(sites)} sites from {SITES_FILE}")
            return sites

        except Exception as e:
            logger.error(f"Error reading sites file: {e}")
            return []

    def crawl_sites(self):
        """Main crawling method with retry mechanism for failed sites"""
        logger.info("=" * 40)
        logger.info(f"Starting parallel site crawling process ({PARALLEL_SITES} sites in parallel)")
        logger.info("=" * 40)

        sites_list = self.read_sites()
        if not sites_list:
            logger.warning("No sites to crawl")
            return

        logger.info(f"Will crawl {len(sites_list)} sites with depth {CRAWL_DEPTH}")

        # Initialize retry tracking
        failed_sites = []  # Sites that failed to crawl
        site_failure_counts = {}  # Track failure count per site
        max_retries = 10
        success_count = 0
        total_failure_count = 0

        # Create working queue starting with all sites
        working_queue = sites_list.copy()
        retry_queue = []

        while working_queue or retry_queue:
            # If working queue is empty, move retry queue to working queue
            if not working_queue and retry_queue:
                logger.info(f"🔄 Starting retry round with {len(retry_queue)} failed sites")
                working_queue = retry_queue.copy()
                retry_queue = []

            # Process sites in batches
            for i in range(0, len(working_queue), PARALLEL_SITES):
                batch = working_queue[i:i + PARALLEL_SITES]
                logger.info(f"Processing batch {i // PARALLEL_SITES + 1}: {len(batch)} sites")

                # Submit batch to thread pool
                future_to_site = {
                    self.executor.submit(self._crawl_single_site, site_url, i + idx + 1, len(working_queue)): site_url
                    for idx, site_url in enumerate(batch)
                }

                # Collect results
                for future in as_completed(future_to_site):
                    site_url = future_to_site[future]
                    try:
                        success = future.result()
                        if success:
                            success_count += 1
                            # Remove from failure tracking if it succeeded
                            if site_url in site_failure_counts:
                                logger.info(
                                    f"✅ {site_url} succeeded after {site_failure_counts[site_url]} previous failures")
                                del site_failure_counts[site_url]
                        else:
                            # Track failure
                            site_failure_counts[site_url] = site_failure_counts.get(site_url, 0) + 1
                            failure_count = site_failure_counts[site_url]

                            if failure_count < max_retries:
                                logger.warning(
                                    f"❌ {site_url} failed ({failure_count}/{max_retries}) - adding to retry queue")
                                retry_queue.append(site_url)
                            else:
                                logger.error(
                                    f"🚫 {site_url} failed {max_retries} times - removing from crawl list permanently")
                                failed_sites.append(site_url)
                                total_failure_count += 1

                    except Exception as e:
                        # Track unexpected exceptions
                        site_failure_counts[site_url] = site_failure_counts.get(site_url, 0) + 1
                        failure_count = site_failure_counts[site_url]

                        logger.error(f"💥 Exception processing site {site_url}: {e}")

                        if failure_count < max_retries:
                            logger.warning(
                                f"❌ {site_url} exception ({failure_count}/{max_retries}) - adding to retry queue")
                            retry_queue.append(site_url)
                        else:
                            logger.error(
                                f"🚫 {site_url} failed {max_retries} times with exceptions - removing permanently")
                            failed_sites.append(site_url)
                            total_failure_count += 1

                # Brief pause between batches
                time.sleep(2)
                SessionLocal.remove()

            # Clear working queue after processing
            working_queue = []

            # Add delay before retry round if there are sites to retry
            if retry_queue:
                logger.info(f"⏳ Waiting 30 seconds before retry round...")
                time.sleep(30)

        # Final reporting
        logger.info("=" * 50)
        logger.info(f"🏁 CRAWLING COMPLETED")
        logger.info(f"✅ Successful sites: {success_count}")
        logger.info(
            f"🔄 Sites with retries that eventually succeeded: {len([s for s in site_failure_counts.keys() if s not in failed_sites])}")
        logger.info(f"🚫 Permanently failed sites: {len(failed_sites)}")
        logger.info(f"📊 Total attempts made: {success_count + sum(site_failure_counts.values())}")

        if failed_sites:
            logger.warning("Sites that failed permanently:")
            for site in failed_sites:
                logger.warning(f"  - {site} (failed {max_retries} times)")

        logger.info("=" * 50)

    def _crawl_single_site(self, site_url, site_number, total_sites):
        """Crawl a single site in its own thread with proper session management."""
        # Get thread-local session for this worker thread
        thread_session = self._get_thread_session()

        try:
            logger.info(f"🔍 [{site_number}/{total_sites}] Starting crawl: {site_url}")

            # Get or create site in this thread's session
            site = self.get_or_create_site(site_url, session=thread_session)

            # Get appropriate session for this site type
            session_result = self.get_appropriate_session(site_url)
            if session_result is None or len(session_result) != 2 or session_result[0] is None:
                logger.error(f"❌ No working proxy for {site_url}")
                return
            session, proxy_type = session_result
            if not session:
                logger.error(f"❌ No working proxy for {site_url}")
                return

            visited_urls = set()
            pages_to_crawl = [(site_url, 0)]  # (url, depth)
            pages_crawled = 0

            while pages_to_crawl and pages_crawled < MAX_PAGES_PER_SITE:
                current_url, depth = pages_to_crawl.pop(0)

                if current_url in visited_urls or depth > CRAWL_DEPTH:
                    continue

                visited_urls.add(current_url)

                try:
                    logger.info(f"📄 [{site_number}/{total_sites}] Crawling page: {current_url} (depth: {depth})")

                    # Make request using appropriate proxy session
                    response = session.get(current_url, timeout=30)
                    response.raise_for_status()

                    # Parse content
                    soup = BeautifulSoup(response.content, 'html.parser')
                    title = soup.find('title')
                    title_text = title.get_text().strip() if title else "No Title"
                    content_text = soup.get_text()

                    # Save page to database using thread session
                    page = Page(
                        site_id=site.id,
                        url=current_url,
                        title=title_text,
                        content_text=content_text,
                        html_content=str(soup),
                        depth=depth
                    )

                    thread_session.add(page)
                    thread_session.commit()

                    logger.info(f"✅ [{site_number}/{total_sites}] Page saved: {title_text}")

                    # Download and store media files
                    try:
                        self._download_and_store_media_parallel(page.id, current_url, 'webpage_media',
                                                                'Media from webpage', thread_session)
                    except Exception as media_error:
                        logger.error(f"⚠️ Media download failed for {current_url}: {media_error}")

                    # Extract links for next level crawling
                    if depth < CRAWL_DEPTH:
                        new_links = self._extract_links_from_page(soup, current_url)
                        pages_to_crawl.extend([(link, depth + 1) for link in new_links])

                    pages_crawled += 1

                    # Update site's last_crawled timestamp in thread session
                    site.last_crawled = datetime.utcnow()
                    thread_session.commit()

                except requests.exceptions.RequestException as req_error:
                    logger.error(f"❌ Request failed for {current_url}: {req_error}")
                    continue
                except Exception as page_error:
                    logger.error(f"❌ Error processing page {current_url}: {page_error}")
                    thread_session.rollback()
                    continue

            logger.info(f"🎯 [{site_number}/{total_sites}] Completed crawling {site_url}: {pages_crawled} pages")

        except Exception as site_error:
            logger.error(f"❌ Error crawling {site_url} with tor: {site_error}")
            thread_session.rollback()
        finally:
            # Clean up thread session
            thread_session.close()

    def _download_and_store_media_parallel(self, page_id, media_url, media_type, description, db_session):
        """Enhanced media download with proper session management and large file handling"""
        logger.info(f"🔽 Starting download: {media_url}")

        try:
            # Validate URL
            if not media_url or not media_url.startswith(('http://', 'https://')):
                logger.warning(f"⚠️ Invalid media URL: {media_url}")
                return False

            # Check if already downloaded
            existing = db_session.query(MediaFile).filter_by(page_id=page_id, url=media_url).first()
            if existing:
                logger.info(f"📋 Media already exists in DB: {media_url}")
                return True

            # Download the media file
            logger.info(f"🌐 Downloading from: {media_url}")

            # Get appropriate session for the URL - FIXED: properly handle the tuple return
            session_result = self.get_appropriate_session(media_url)
            if session_result is None or len(session_result) != 2 or session_result[0] is None:
                logger.error(f"❌ No appropriate session available for {media_url}")
                return False

            download_session, proxy_type = session_result
            logger.info(f"🔗 Using {proxy_type} session for media download")

            response = download_session.get(media_url, stream=True, timeout=30)
            response.raise_for_status()

            # Get content info
            content_type = response.headers.get('content-type', '')
            content_length = response.headers.get('content-length')

            logger.info(f"📊 Content-Type: {content_type}, Size: {content_length}")

            # Categorize media type
            categorized_type = self._categorize_media_type(media_url, content_type)
            size_limit = self._get_size_limit_for_media_type(categorized_type)

            # Check size limit
            if content_length and int(content_length) > size_limit:
                logger.warning(f"⚠️ Media too large: {content_length} > {size_limit}")
                return False

            # Read content
            content = b''
            downloaded_size = 0

            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    content += chunk
                    downloaded_size += len(chunk)

                    # Check size limit during download
                    if downloaded_size > size_limit:
                        logger.warning(f"⚠️ Download size exceeded limit: {downloaded_size} > {size_limit}")
                        return False

            logger.info(f"✅ Downloaded {downloaded_size} bytes")

            # Generate MinIO storage info
            bucket_name = self._get_minio_bucket_for_media_type(categorized_type)
            object_name = self._generate_minio_object_name(media_url, categorized_type)

            logger.info(f"📦 Storing in MinIO - Bucket: {bucket_name}, Object: {object_name}")

            # Ensure bucket exists
            try:
                if not self.minio_client.bucket_exists(bucket_name):
                    logger.info(f"🪣 Creating bucket: {bucket_name}")
                    self.minio_client.make_bucket(bucket_name)
            except Exception as bucket_error:
                logger.error(f"❌ Bucket creation failed: {bucket_error}")
                return False

            # Upload to MinIO
            try:
                from io import BytesIO

                content_stream = BytesIO(content)
                self.minio_client.put_object(
                    bucket_name,
                    object_name,
                    content_stream,
                    length=len(content),
                    content_type=content_type
                )
                logger.info(f"✅ Uploaded to MinIO: {bucket_name}/{object_name}")

            except Exception as upload_error:
                logger.error(f"❌ MinIO upload failed: {upload_error}")
                return False

            # FIXED: Handle large files - don't store content in DB if > 1MB
            content_for_db = None if len(content) > 1048576 else content  # 1MB threshold

            if content_for_db is None:
                logger.info(f"📊 Large file ({len(content)} bytes) - storing only metadata in DB")

            # Save to database with proper error handling
            try:
                media_file = MediaFile(
                    page_id=page_id,
                    url=media_url,
                    file_type=content_type,
                    content=content_for_db,  # FIXED: None for large files to avoid DB error
                    description=description,
                    size_bytes=len(content),
                    filename=object_name,
                    media_category=categorized_type,
                    minio_bucket=bucket_name,
                    minio_object_name=object_name,
                    downloaded_at=datetime.utcnow()
                )

                db_session.add(media_file)
                db_session.commit()

                logger.info(f"✅ Saved to database: MediaFile ID {media_file.id}")
                return True

            except Exception as db_error:
                logger.error(f"❌ Database save failed: {db_error}")
                db_session.rollback()

                # If it's a "Data too long" error, try again without content
                if "Data too long" in str(db_error) and content_for_db is not None:
                    logger.info("🔄 Retrying without content in database...")
                    try:
                        media_file = MediaFile(
                            page_id=page_id,
                            url=media_url,
                            file_type=content_type,
                            content=None,  # Don't store content in DB
                            description=description,
                            size_bytes=len(content),
                            filename=object_name,
                            media_category=categorized_type,
                            minio_bucket=bucket_name,
                            minio_object_name=object_name,
                            downloaded_at=datetime.utcnow()
                        )

                        db_session.add(media_file)
                        db_session.commit()

                        logger.info(f"✅ Saved to database (metadata only): MediaFile ID {media_file.id}")
                        return True

                    except Exception as retry_error:
                        logger.error(f"❌ Retry also failed: {retry_error}")
                        db_session.rollback()
                        return False

                return False

        except Exception as e:
            logger.error(f"❌ Media download failed for {media_url}: {e}")
            import traceback
            traceback.print_exc()
            db_session.rollback()
            return False

    def _extract_links_from_page(self, soup, base_url):
        """Extract valid links from a page"""
        links = []
        for link in soup.find_all('a', href=True):
            href = link['href']
            full_url = urljoin(base_url, href)

            # Filter out non-HTTP links
            if full_url.startswith(('http://', 'https://')):
                links.append(full_url)

        return list(set(links))  # Remove duplicates

    def _download_single_media(self, media_url, media_type, page_id, page_url, session):
        """Download a single media file and store it."""
        try:
            # Get appropriate session for the media URL
            session_result = self.get_appropriate_session(media_url)
            if session_result is None or len(session_result) != 2 or session_result[0] is None:
                logger.error(f"❌ No working proxy for media: {media_url}")
                return
            proxy_session, proxy_type = session_result
            if not proxy_session:
                logger.error(f"❌ No working proxy for media: {media_url}")
                return

            # Download the media
            response = proxy_session.get(media_url, timeout=30, stream=True)
            response.raise_for_status()

            # Check content type and size
            content_type = response.headers.get('content-type', '').lower()
            content_length = int(response.headers.get('content-length', 0))

            # Size limits based on media type
            size_limits = {
                'image': MAX_IMAGE_SIZE,
                'audio': MAX_AUDIO_SIZE,
                'video': MAX_VIDEO_SIZE
            }

            max_size = size_limits.get(media_type, MAX_IMAGE_SIZE)
            if content_length > max_size:
                logger.warning(f"⚠️ Media too large ({content_length} bytes): {media_url}")
                return

            # Read content
            content = response.content
            if len(content) > max_size:
                logger.warning(f"⚠️ Downloaded content too large ({len(content)} bytes): {media_url}")
                return

            # Generate filename
            parsed_url = urlparse(media_url)
            original_filename = os.path.basename(parsed_url.path) or "unknown_file"
            timestamp = int(time.time())
            filename = f"{media_type}/{timestamp}_{hashlib.md5(media_url.encode()).hexdigest()}.{original_filename.split('.')[-1] if '.' in original_filename else 'bin'}"

            # Store in MinIO
            minio_bucket = None
            minio_object_name = None
            if self.minio_client:
                try:
                    if media_type == 'image':
                        minio_bucket = MINIO_BUCKET_IMAGES
                    elif media_type == 'audio':
                        minio_bucket = MINIO_BUCKET_AUDIO
                    elif media_type == 'video':
                        minio_bucket = MINIO_BUCKET_VIDEO
                    else:
                        minio_bucket = MINIO_BUCKET_OTHER

                    minio_object_name = filename

                    # Upload to MinIO
                    self.minio_client.put_object(
                        minio_bucket,
                        minio_object_name,
                        io.BytesIO(content),
                        len(content),
                        content_type=content_type
                    )
                    logger.info(f"📤 Uploaded to MinIO: {minio_bucket}/{minio_object_name}")

                except Exception as minio_error:
                    logger.error(f"❌ MinIO upload failed: {minio_error}")

            # For large files, don't store content in database
            content_for_db = None if len(content) > 1000000 else content  # 1MB threshold

            # Save to database using the provided session
            media_file = MediaFile(
                page_id=page_id,
                url=media_url,
                file_type=content_type,
                content=content_for_db,
                description=f"Media from {page_url}",
                size_bytes=len(content),
                filename=filename,
                media_category=media_type,
                minio_bucket=minio_bucket,
                minio_object_name=minio_object_name
            )

            session.add(media_file)
            session.commit()

            logger.info(f"✅ Media saved: {media_url} ({len(content)} bytes)")

        except Exception as e:
            logger.error(f"❌ Error downloading media {media_url}: {e}")
            session.rollback()

    def _categorize_media_type(self, url, content_type=None):
        """Categorize media type based on URL and content type"""
        url_lower = url.lower()

        # Image types
        if any(ext in url_lower for ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg']):
            return 'image'

        # Video types
        if any(ext in url_lower for ext in ['.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mkv']):
            return 'video'

        # Audio types
        if any(ext in url_lower for ext in ['.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac']):
            return 'audio'

        # Check content type if available
        if content_type:
            if content_type.startswith('image/'):
                return 'image'
            elif content_type.startswith('video/'):
                return 'video'
            elif content_type.startswith('audio/'):
                return 'audio'

        return 'other'

    def _get_size_limit_for_media_type(self, media_type):
        """Get size limit for specific media type"""
        if media_type == 'image':
            return MAX_IMAGE_SIZE
        elif media_type == 'audio':
            return MAX_AUDIO_SIZE
        elif media_type == 'video':
            return MAX_VIDEO_SIZE
        else:
            return MAX_IMAGE_SIZE  # Default limit for other types

    def _get_minio_bucket_for_media_type(self, media_type):
        """Get MinIO bucket name for media type"""
        if media_type == 'image':
            return MINIO_BUCKET_IMAGES
        elif media_type == 'audio':
            return MINIO_BUCKET_AUDIO
        elif media_type == 'video':
            return MINIO_BUCKET_VIDEO
        else:
            return MINIO_BUCKET_OTHER

    def _generate_minio_object_name(self, media_url, page_id):
        """Generate unique object name for MinIO storage"""
        # Create hash of URL for uniqueness
        url_hash = hashlib.md5(media_url.encode()).hexdigest()

        # Extract extension
        parsed_url = urlparse(media_url)
        path = parsed_url.path
        extension = os.path.splitext(path)[1] if path else ''

        # Generate object name
        timestamp = int(time.time())
        object_name = f"page_{page_id}/{timestamp}_{url_hash}{extension}"

        return object_name

    def _extract_all_media_files_parallel(self, page, soup, db_session):
        """Enhanced media extraction with detailed logging"""
        logger.info(f"🔽 Extracting media files for page {page.id}: {page.url}")

        if not DOWNLOAD_ALL_MEDIA:
            logger.warning("⚠️ DOWNLOAD_ALL_MEDIA is disabled - skipping media download")
            return 0

        # Test MinIO connection first
        if not self.minio_client:
            logger.error("❌ MinIO client not initialized")
            return 0

        try:
            # Test MinIO connection
            buckets = list(self.minio_client.list_buckets())
            logger.info(f"✅ MinIO connection OK - found {len(buckets)} buckets")
        except Exception as e:
            logger.error(f"❌ MinIO connection failed: {e}")
            return 0

        media_files = []
        downloaded_count = 0

        # Extract images
        images = soup.find_all('img', src=True)
        logger.info(f"Processing {len(images)} images...")

        for img in images:
            src = img.get('src')
            if src:
                # Make absolute URL
                from urllib.parse import urljoin
                absolute_url = urljoin(page.url, src)
                media_files.append({
                    'url': absolute_url,
                    'type': 'image',
                    'alt_text': img.get('alt', ''),
                    'element': 'img'
                })

        # Extract videos
        videos = soup.find_all(['video', 'source'], src=True)
        logger.info(f"Processing {len(videos)} videos...")

        for vid in videos:
            src = vid.get('src')
            if src:
                absolute_url = urljoin(page.url, src)
                media_files.append({
                    'url': absolute_url,
                    'type': 'video',
                    'alt_text': '',
                    'element': vid.name
                })

        # Extract audio
        audios = soup.find_all(['audio', 'source'], src=True)
        logger.info(f"Processing {len(audios)} audio files...")

        for aud in audios:
            src = aud.get('src')
            if src and ('audio' in aud.get('type', '') or aud.name == 'audio'):
                absolute_url = urljoin(page.url, src)
                media_files.append({
                    'url': absolute_url,
                    'type': 'audio',
                    'alt_text': '',
                    'element': aud.name
                })

        # Extract downloadable files
        links = soup.find_all('a', href=True)
        file_extensions = ['.pdf', '.doc', '.docx', '.txt', '.zip', '.rar']

        for link in links:
            href = link.get('href')
            if href and any(ext in href.lower() for ext in file_extensions):
                absolute_url = urljoin(page.url, href)
                media_files.append({
                    'url': absolute_url,
                    'type': 'document',
                    'alt_text': link.get_text(strip=True),
                    'element': 'a'
                })

        logger.info(f"📊 Found {len(media_files)} total media files to download")

        if not media_files:
            logger.warning("⚠️ No media files found to download")
            return 0

        # Limit media files if configured
        if MAX_MEDIA_PER_PAGE > 0:
            media_files = media_files[:MAX_MEDIA_PER_PAGE]
            logger.info(f"📊 Limited to {len(media_files)} media files due to MAX_MEDIA_PER_PAGE setting")

        # Download each media file
        for i, media_info in enumerate(media_files, 1):
            logger.info(f"🔽 Downloading media {i}/{len(media_files)}: {media_info['url']}")

            try:
                success = self._download_and_store_media_parallel(
                    page.id,
                    media_info['url'],
                    media_info['type'],
                    media_info['alt_text'],
                    db_session  # Pass the database session
                )

                if success:
                    downloaded_count += 1
                    logger.info(f"✅ Downloaded media {i}/{len(media_files)}")
                else:
                    logger.warning(f"⚠️ Failed to download media {i}/{len(media_files)}")

            except Exception as e:
                logger.error(f"❌ Error downloading media {i}/{len(media_files)}: {e}")

        logger.info(f"✅ Media extraction completed: {downloaded_count}/{len(media_files)} files downloaded")
        return downloaded_count

    def process_pages_for_research(self):
        """Process pages for AI research analysis"""
        logger.info("Processing pages for research target 1")
        # Placeholder for AI analysis
        time.sleep(1)
        logger.info("✓ Research processing completed")

    def research_reporting(self):
        """Generate research reports"""
        logger.info("Generating research reports")
        logger.info("Generating research reports...")
        time.sleep(1)
        logger.info("✓ Research reporting completed")

    def setup_enhanced_database(self):
        """Create enhanced database tables for research"""
        try:
            from db_models import Base
            Base.metadata.create_all(engine)
            logger.info("Enhanced database schema created successfully")
            return True
        except Exception as e:
            logger.error(f"Error setting up enhanced database: {e}")
            return False

    def create_research_target(self, name, description, keywords=None, target_domains=None, research_goals=None,
                               priority=1):
        """Create a new research target"""
        try:
            from db_models import ResearchTarget

            research_target = ResearchTarget(
                name=name,
                description=description,
                keywords=keywords or [],
                target_domains=target_domains or [],
                research_goals=research_goals or [],
                priority=priority,
                active=True,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )

            session = self._get_thread_session()
            session.add(research_target)
            session.commit()

            logger.info(f"Created research target: {name}")
            return research_target

        except Exception as e:
            logger.error(f"Error creating research target: {e}")
            session.rollback()
            return None

    def _cleanup_resources(self):
        """Clean up all resources"""
        logger.info("Cleaning up resources...")

        # Close clearnet session
        if hasattr(self, 'clearnet_session') and self.clearnet_session:
            self.clearnet_session.close()

        # Close all Tor sessions
        if hasattr(self, 'tor_sessions'):
            for tor_session_info in self.tor_sessions:
                proxy_type, session = tor_session_info[0], tor_session_info[1]
                if session:
                    session.close()

        # Close all I2P sessions
        if hasattr(self, 'i2p_sessions'):
            for i2p_session_info in self.i2p_sessions:
                proxy_type = i2p_session_info[0]
                session = i2p_session_info[1]
                if session:
                    session.close()

        logger.info("✓ Resources cleaned up successfully")

    def _wait_for_i2p_bootstrap(self, max_wait_minutes=15):
        """Wait for I2P to bootstrap with better error handling"""
        logger.info(f"Waiting for I2P bootstrap (max {max_wait_minutes} minutes)...")

        start_time = time.time()
        max_wait_seconds = max_wait_minutes * 60

        while time.time() - start_time < max_wait_seconds:
            try:
                # Check I2P console for router count
                response = requests.get(
                    "http://0.0.0.0:7070/netdb",
                    timeout=5
                )

                if response.status_code == 200:
                    # Parse router count from console
                    router_count = self._parse_router_count(response.text)

                    if router_count > 5:  # Need at least 5 routers for stability
                        logger.info(f"✓ I2P bootstrapped with {router_count} routers")
                        return True
                    else:
                        logger.info(f"I2P still bootstrapping ({router_count} routers)...")

                # Try alternative bootstrap method
                if time.time() - start_time > 300:  # After 5 minutes
                    self._force_i2p_reseed()

            except Exception as e:
                logger.debug(f"Bootstrap check failed: {e}")

            time.sleep(30)

        logger.warning("I2P bootstrap timeout - continuing with limited functionality")
        return False

    def _parse_router_count(self, html_content):
        """Extract router count from I2P console HTML"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, 'html.parser')

            # Look for router count in various locations
            for element in soup.find_all(['td', 'span', 'div']):
                text = element.get_text()
                if 'routers' in text.lower():
                    import re
                    numbers = re.findall(r'\d+', text)
                    if numbers:
                        return int(numbers[0])
            return 0
        except:
            return 0

    def _parallel_reseed_download(self):
        """Download router info from multiple reseed servers in parallel"""
        logger.info("Starting parallel I2P reseed...")

        # Your working reseed URLs
        reseed_urls = [
            "https://reseed.diva.exchange/",
            "https://reseed.i2pgit.org/",
            "https://i2p.novg.net/",
            "https://reseed.memcpy.io/",
            "https://i2pseed.creativecowpat.net:8443/",
            "https://reseed.onion.im/",
            "https://reseed.atomike.ninja/",
            "https://banana.incognet.io/"
        ]

        success_queue = Queue()
        router_dir = "/var/lib/i2pd/netDb"
        os.makedirs(router_dir, exist_ok=True)

        def download_from_server(url):
            """Download from a single reseed server"""
            session = requests.Session()
            # Use direct clearnet connection, not Tor
            session.headers.update({
                'User-Agent': 'Mozilla/5.0 (compatible; I2P reseed client)'
            })

            endpoints = [
                f"{url.rstrip('/')}/",
                f"{url.rstrip('/')}/netDb/",
                f"{url.rstrip('/')}/routerInfo.zip"
            ]

            for endpoint in endpoints:
                try:
                    logger.debug(f"Trying {endpoint}")
                    response = session.get(endpoint, timeout=15, stream=True)

                    if response.status_code == 200:
                        content = response.content
                        if len(content) > 1000:  # Valid reseed data
                            success_queue.put((url, endpoint, content))
                            logger.info(f"✓ Fast reseed from {url}")
                            return True

                except Exception as e:
                    logger.debug(f"Failed {endpoint}: {e}")
                    continue

            return False

        # Launch parallel downloads with max 6 threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(download_from_server, url) for url in reseed_urls]

            # Wait for first success or 30 seconds max
            start_time = time.time()
            while time.time() - start_time < 30:
                if not success_queue.empty():
                    url, endpoint, content = success_queue.get()

                    # Save the reseed data
                    try:
                        session = requests.Session()
                        response = session.get(endpoint)
                        if endpoint.endswith('.zip') or 'zip' in response.headers.get('content-type', ''):
                            file_path = f"{router_dir}/routerInfo.zip"
                            with open(file_path, "wb") as f:
                                f.write(content)

                            # Extract zip
                            import zipfile
                            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                                zip_ref.extractall(router_dir)

                            logger.info(f"✓ Parallel reseed successful from {url} in {time.time() - start_time:.1f}s")
                        else:
                            # Save raw router data
                            with open(f"{router_dir}/reseed_data.dat", "wb") as f:
                                f.write(content)
                            logger.info(f"✓ Router data downloaded from {url}")

                        # Cancel remaining downloads
                        for future in futures:
                            future.cancel()

                        return True

                    except Exception as e:
                        logger.debug(f"Failed to save reseed data: {e}")
                        continue

                time.sleep(0.5)

        logger.warning("Parallel reseed failed - no servers responded in time")
        return False

    def _force_i2p_reseed(self):
        """Force I2P reseed using fast parallel methods"""
        logger.info("Forcing I2P reseed...")

        # First check if I2P is already working
        if self._test_i2p_connectivity():
            logger.info("✓ I2P already working, skipping reseed")
            return True

        # Method 1: Fast parallel reseed download
        logger.info("Attempting fast parallel reseed...")
        if self._parallel_reseed_download():
            logger.info("✓ Parallel reseed successful")

            # Give I2P a moment to process the new data
            time.sleep(5)

            # Test if it worked
            if self._test_i2p_connectivity():
                return True

        # Method 2: Restart I2P with optimized config
        logger.info("Attempting I2P restart with aggressive bootstrap config...")
        self._restart_i2p_with_bootstrap()

        return False

    def _test_i2p_connectivity(self):
        """Quick test if I2P is working"""
        if not hasattr(self, 'i2p_session') or not self.i2p_session:
            return False

        try:
            # Quick test to known I2P sites
            test_sites = ["http://stats.i2p/", "http://reg.i2p/"]
            for site in test_sites:
                try:
                    response = self.i2p_session.get(site, timeout=8)
                    if response.status_code == 200:
                        logger.debug(f"✓ I2P connectivity confirmed via {site}")
                        return True
                except:
                    continue
            return False
        except:
            return False

    def test_minio_connection(self):
        """Test MinIO connection and create buckets if needed"""
        logger.info("🧪 Testing MinIO connection...")

        if not self.minio_client:
            logger.error("❌ MinIO client not initialized")
            return False

        try:
            # Test connection by listing buckets
            buckets = list(self.minio_client.list_buckets())
            logger.info(f"✅ MinIO connected - found {len(buckets)} buckets")

            # Create required buckets if they don't exist
            required_buckets = [
                MINIO_BUCKET_IMAGES,
                MINIO_BUCKET_AUDIO,
                MINIO_BUCKET_VIDEO,
                MINIO_BUCKET_OTHER
            ]

            for bucket_name in required_buckets:
                try:
                    if not self.minio_client.bucket_exists(bucket_name):
                        logger.info(f"🪣 Creating bucket: {bucket_name}")
                        self.minio_client.make_bucket(bucket_name)
                        logger.info(f"✅ Created bucket: {bucket_name}")
                    else:
                        logger.info(f"✅ Bucket exists: {bucket_name}")
                except Exception as bucket_error:
                    logger.error(f"❌ Error with bucket {bucket_name}: {bucket_error}")

            return True

        except Exception as e:
            logger.error(f"❌ MinIO connection test failed: {e}")
            return False

    def _download_router_info_via_tor(self):
        """Download router info via Tor proxy"""
        reseed_urls = [
            "https://netdb.i2p.rocks/export/routerInfo.zip",
            "https://reseed.i2p-projekt.de/routerInfo.zip",
            "https://reseed.memcpy.io/routerInfo.zip",
            "https://download.xxlspeed.com/routerInfo.zip"
        ]

        if not hasattr(self, 'tor_session') or not self.tor_session:
            logger.warning("Tor session not available for router info download")
            return False

        for url in reseed_urls:
            try:
                logger.info(f"Downloading router info from {url}...")
                response = self.tor_session.get(url, timeout=30)

                if response.status_code == 200:
                    # Save to I2P directory
                    import os
                    router_dir = "/var/lib/i2pd/netDb"
                    os.makedirs(router_dir, exist_ok=True)

                    with open(f"{router_dir}/routerInfo.zip", "wb") as f:
                        f.write(response.content)

                    # Extract if it's a zip file
                    if url.endswith('.zip'):
                        import zipfile
                        with zipfile.ZipFile(f"{router_dir}/routerInfo.zip", 'r') as zip_ref:
                            zip_ref.extractall(router_dir)

                    return True

            except Exception as e:
                logger.debug(f"Failed to download from {url}: {e}")

        return False

    def _restart_i2p_with_bootstrap(self):
        """Restart I2P with bootstrap-friendly configuration"""
        try:
            import subprocess
            import os

            # Kill existing I2P process
            subprocess.run(["pkill", "-f", "i2pd"], check=False)
            time.sleep(5)

            # Create bootstrap config
            bootstrap_config = """
    # Bootstrap-friendly I2P configuration
    ipv4 = true
    ipv6 = false
    notransit = false
    floodfill = false
    nat = true

    # HTTP Proxy
    httpproxy.enabled = true
    httpproxy.address = 0.0.0.0
    httpproxy.port = 4444
    httpproxy.outproxy = http://false.i2p

    # Web console
    http.enabled = true
    http.address = 0.0.0.0
    http.port = 7070

    # Aggressive bootstrap settings
    reseed.verify = false
    reseed.threshold = 5
    reseed.urls = https://reseed.i2p-projekt.de/,https://reseed.memcpy.io/,https://download.xxlspeed.com/,https://netdb.i2p.rocks/

    # More aggressive network settings
    bandwidth = 1024
    share = 50
    limits.transittunnels = 100
    exploratory.inbound.quantity = 6
    exploratory.outbound.quantity = 6
    """

            # Write bootstrap config
            with open("/etc/i2pd/bootstrap.conf", "w") as f:
                f.write(bootstrap_config)

            # Start I2P with bootstrap config
            subprocess.Popen([
                "i2pd",
                "--conf=/etc/i2pd/bootstrap.conf",
                "--datadir=/var/lib/i2pd"
            ])

            logger.info("I2P restarted with bootstrap configuration")
            time.sleep(30)  # Give it time to start

        except Exception as e:
            logger.error(f"Failed to restart I2P: {e}")

    def _gentle_i2p_health_check(self):
        """Patient I2P health check that doesn't disrupt bootstrap"""
        try:
            # Check if I2P process is running
            if not self._is_i2p_process_running():
                logger.warning("I2P process not detected")
                return False

            # Check console responsiveness (be patient)
            try:
                response = requests.get("http://0.0.0.0:7070", timeout=10)
                content_size = len(response.content)

                if content_size < 100:
                    logger.info(
                        f"I2P console returning minimal content ({content_size} bytes) - likely still initializing")
                    return False

                # Look for positive indicators without being picky about errors
                content = response.text.lower()
                if "router console" in content or "i2p" in content:
                    logger.info(f"✓ I2P console responsive ({content_size} bytes)")

                    # Try to extract router count for info (don't fail if we can't)
                    import re
                    router_match = re.search(r'(\d+)\s+(?:known\s+)?routers?', content, re.IGNORECASE)
                    if router_match:
                        router_count = int(router_match.group(1))
                        logger.info(f"I2P knows about {router_count} routers")
                        if router_count >= 10:
                            logger.info("Router count looks healthy")
                        else:
                            logger.info("Router count low - still building network knowledge")

                    return True
                else:
                    logger.info("I2P console accessible but content unexpected")
                    return False

            except requests.exceptions.RequestException as e:
                logger.info(f"I2P console not ready: {e}")
                return False

        except Exception as e:
            logger.warning(f"I2P health check error: {e}")
            return False

    def _is_i2p_process_running(self):
        """Check if I2P process is running without being disruptive"""
        try:
            import subprocess
            result = subprocess.run(
                ["pgrep", "-f", "i2pd"],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0 and result.stdout.strip()
        except:
            return False

    def _patient_i2p_wait(self, max_minutes=15):
        """Patient wait for I2P to become ready"""
        logger.info(f"Waiting patiently for I2P to initialize (up to {max_minutes} minutes)...")

        start_time = time.time()
        max_wait = max_minutes * 60
        check_interval = 30  # Check every 30 seconds

        last_status = None

        while (time.time() - start_time) < max_wait:
            current_status = self._gentle_i2p_health_check()

            # Only log if status changed to reduce noise
            if current_status != last_status:
                if current_status:
                    logger.info("✓ I2P appears ready")
                    return True
                else:
                    elapsed = int((time.time() - start_time) / 60)
                    logger.info(f"I2P still initializing... ({elapsed}/{max_minutes} minutes)")

            last_status = current_status

            if current_status:
                # Give it a bit more time to stabilize
                logger.info("I2P looks ready, giving it 30 more seconds to stabilize...")
                time.sleep(30)
                if self._gentle_i2p_health_check():
                    return True

            time.sleep(check_interval)

        logger.warning(f"I2P did not become ready within {max_minutes} minutes")
        return False

    def _gentle_proxy_test(self):
        """Test proxies without aggressive timeouts"""
        logger.info("=== Gentle Proxy Testing ===")

        tor_working = False
        i2p_working = False

        # Test Tor with reasonable timeout
        logger.info("Testing Tor proxy...")
        try:
            response = requests.get(
                "http://httpbin.org/ip",
                proxies={"http": f"socks5://127.0.0.1:{TOR_SOCKS_PORT}"},
                timeout=15  # More generous timeout
            )
            if response.status_code == 200:
                logger.info("✓ Tor proxy working")
                tor_working = True
            else:
                logger.info(f"Tor proxy returned status {response.status_code}")
        except Exception as e:
            logger.info(f"Tor proxy not ready: {str(e)[:100]}")

        # Test I2P with patience
        logger.info("Testing I2P proxy...")
        try:
            response = requests.get(
                "http://httpbin.org/ip",
                proxies={"http": f"http://0.0.0.0:{I2P_HTTP_PROXY_PORT}"},
                timeout=25  # Very generous timeout for I2P
            )
            if response.status_code == 200:
                logger.info("✓ I2P proxy working")
                i2p_working = True
            else:
                logger.info(f"I2P proxy returned status {response.status_code}")
        except Exception as e:
            logger.info(f"I2P proxy not ready: {str(e)[:100]}")

        # Check if we should continue based on proxy status
        if not tor_working and not i2p_working:
            logger.warning("❌ CRITICAL: Both Tor and I2P proxies failed - cannot access .onion or .i2p sites")
            logger.warning("Crawler will wait for proxy services to become available...")
            logger.warning("DO NOT restart proxy processes - waiting for initialization...")
            return False, False  # Return early to trigger waiting behavior

        return tor_working, i2p_working

    def crawl_sites_from_file(self, sites_file):
        """Enhanced crawl with proxy readiness check"""
        logger.info("========================================")
        logger.info("Starting parallel site crawling process")
        logger.info("========================================")

        # Check proxy readiness first
        tor_ok, i2p_ok = self._gentle_proxy_test()

        # If both proxies failed, wait and retry instead of proceeding
        if not tor_ok and not i2p_ok:
            logger.error("❌ Both proxy services are unavailable")
            logger.info("Waiting for proxy services to initialize...")

            # Wait and retry mechanism
            max_wait_cycles = 30  # 30 cycles = 15 minutes total
            wait_cycle = 0

            while wait_cycle < max_wait_cycles:
                logger.info(f"⏳ Waiting for proxy services... (cycle {wait_cycle + 1}/{max_wait_cycles})")
                time.sleep(30)  # Wait 30 seconds between checks

                # Re-test proxies
                tor_ok, i2p_ok = self._gentle_proxy_test()

                if tor_ok or i2p_ok:
                    logger.info("✓ At least one proxy service is now available")
                    break

                wait_cycle += 1

            # If still no proxies after waiting, refuse to continue
            if not tor_ok and not i2p_ok:
                logger.error("❌ ABORTING: No proxy services available after 15 minutes")
                logger.error("Cannot crawl .onion or .i2p sites without working proxies")
                logger.error("Please check Tor and I2P service status manually")
                return False

        # Continue with existing crawling logic
        try:
            if not os.path.exists(sites_file):
                logger.error(f"Sites file not found: {sites_file}")
                return False

            with open(sites_file, 'r') as f:
                sites_list = [line.strip() for line in f if line.strip() and not line.startswith('#')]

            if not sites_list:
                logger.warning("No sites to crawl")
                return False

            logger.info(f"Loaded {len(sites_list)} sites from {sites_file}")
            logger.info(f"Will crawl {len(sites_list)} sites with depth {CRAWL_DEPTH}")

            # Process sites in parallel batches
            parallel_sites = min(PARALLEL_SITES, len(sites_list))
            logger.info(f"Processing batch 1: {parallel_sites} sites")

            # Split sites into batches for parallel processing
            total_batches = math.ceil(len(sites_list) / parallel_sites)
            successful_sites = 0
            failed_sites = 0

            for batch_num in range(total_batches):
                start_idx = batch_num * parallel_sites
                end_idx = min(start_idx + parallel_sites, len(sites_list))
                batch_sites = sites_list[start_idx:end_idx]

                logger.info(f"Processing batch {batch_num + 1}/{total_batches}: {len(batch_sites)} sites")

                # Process this batch of sites in parallel
                with ThreadPoolExecutor(max_workers=parallel_sites) as executor:
                    # Submit crawling tasks for each site in this batch
                    future_to_site = {}
                    for site_url in batch_sites:
                        future = executor.submit(self._crawl_single_site_wrapper, site_url)
                        future_to_site[future] = site_url

                    # Collect results as they complete
                    for future in as_completed(future_to_site):
                        site_url = future_to_site[future]
                        try:
                            success = future.result(timeout=300)  # 5 minute timeout per site
                            if success:
                                successful_sites += 1
                                logger.info(f"✓ Successfully crawled: {site_url}")
                            else:
                                failed_sites += 1
                                logger.warning(f"✗ Failed to crawl: {site_url}")
                        except Exception as e:
                            failed_sites += 1
                            logger.error(f"✗ Exception crawling {site_url}: {str(e)[:100]}")

                # Brief pause between batches to avoid overwhelming the system
                if batch_num < total_batches - 1:
                    logger.info("Pausing 10 seconds between batches...")
                    time.sleep(10)

            # Final summary
            logger.info("========================================")
            logger.info("CRAWLING SUMMARY")
            logger.info("========================================")
            logger.info(f"Total sites processed: {len(sites_list)}")
            logger.info(f"Successfully crawled: {successful_sites}")
            logger.info(f"Failed to crawl: {failed_sites}")
            logger.info(f"Success rate: {successful_sites / len(sites_list) * 100:.1f}%")

            return successful_sites > 0

        except Exception as e:
            logger.error(f"Error in crawl_sites_from_file: {str(e)}")
            return False

    def _crawl_single_site_wrapper(self, site_url):
        """Wrapper method to crawl a single site with proper error handling"""
        try:
            logger.info(f"Starting crawl of: {site_url}")

            # Check if site requires proxy and if appropriate proxy is available
            requires_tor = '.onion' in site_url.lower()
            requires_i2p = '.i2p' in site_url.lower()

            if requires_tor and not getattr(self, 'tor_working', False):
                logger.warning(f"Skipping .onion site (Tor unavailable): {site_url}")
                return False

            if requires_i2p and not getattr(self, 'i2p_working', False):
                logger.warning(f"Skipping .i2p site (I2P unavailable): {site_url}")
                return False

            # Create or get site record
            site_record = self._get_or_create_site(site_url)
            if not site_record:
                logger.error(f"Failed to create site record for: {site_url}")
                return False

            # Crawl the site starting from its main page
            success = self._crawl_page_with_fallback_parallel(site_record, site_url, depth=0)

            if success:
                # Update site's last crawled timestamp
                self._update_site_last_crawled(site_record.id)

            return success

        except Exception as e:
            logger.error(f"Error crawling site {site_url}: {str(e)}")
            return False

    def _crawl_page_with_fallback_parallel(self, site, url, depth=0):
        """Enhanced crawl method with proxy availability check"""
        # Check if URL requires proxy services
        is_onion = '.onion' in url.lower()
        is_i2p = '.i2p' in url.lower()
        if is_onion or is_i2p:
            # Quick proxy check for special domains
            tor_ok, i2p_ok = False, False
            # Light check without full testing
            if is_onion and hasattr(self, 'tor_session'):
                tor_ok = True  # Assume working if session exists
            if is_i2p and hasattr(self, 'i2p_session') and getattr(self, 'i2p_working', False):
                i2p_ok = True  # Use cached status
            if is_onion and not tor_ok:
                logger.warning(f"⏭ Skipping .onion URL (Tor not available): {url}")
                return False
            if is_i2p and not i2p_ok:
                logger.warning(f"⏭ Skipping .i2p URL (I2P not available): {url}")
                return False
        try:
            logger.info(f"📄 Crawling page: {url} (depth: {depth})")
            # Get the CORRECT session for this URL type
            session_result = self.get_appropriate_session(url)

            # Fix: Properly handle the tuple return value
            if session_result is None or len(session_result) != 2 or session_result[0] is None:
                logger.error(f"❌ No appropriate session available for {url}")
                return False

            session, proxy_type = session_result

            # NO FALLBACK to clearnet for .onion/.i2p URLs
            if url.endswith('.onion') and proxy_type != 'tor':
                logger.error(f"❌ Cannot access .onion URL without Tor: {url}")
                return False
            if url.endswith('.i2p') and proxy_type != 'i2p':
                logger.error(f"❌ Cannot access .i2p URL without I2P: {url}")
                return False
            logger.info(f"🌐 Using {proxy_type} session for {url}")
            # Single attempt with correct session - no fallbacks to wrong networks
            try:
                response = session.get(url, timeout=30, verify=False)
                if response.status_code == 200:
                    logger.info(f"✅ Successfully crawled {url} via {proxy_type}")

                    # Ensure we have proper content for BeautifulSoup
                    try:
                        # Handle different content types and encoding issues
                        if hasattr(response, 'content') and response.content:
                            html_content = response.content
                        elif hasattr(response, 'text') and response.text:
                            html_content = response.text.encode('utf-8')
                        else:
                            logger.warning(f"⚠ No content found for {url}")
                            return False

                        # Create BeautifulSoup object with proper error handling
                        soup = BeautifulSoup(html_content, 'html.parser')

                        # Verify soup object was created successfully
                        if not soup or not hasattr(soup, 'find_all'):
                            logger.error(f"❌ Failed to parse HTML content for {url}")
                            return False

                    except Exception as parse_error:
                        logger.error(f"❌ Error parsing HTML for {url}: {parse_error}")
                        return False

                    # Extract page info
                    title = soup.title.string.strip() if soup.title and soup.title.string else "No Title"
                    content = soup.get_text()

                    # Store in database
                    with SessionLocal() as db_session:
                        new_page = Page(
                            site_id=site.id,
                            url=url,
                            title=title,
                            content_text=content,
                            html_content=html_content.decode('utf-8', errors='ignore') if isinstance(html_content,
                                                                                                     bytes) else str(
                                html_content),
                            depth=depth
                        )
                        db_session.add(new_page)
                        db_session.commit()
                        page_id = new_page.id

                    logger.info(f"💾 Stored page {page_id}: {title}")

                    # Extract and process media files
                    if DOWNLOAD_ALL_MEDIA:
                        try:
                            # Get the page object from database for media extraction
                            with SessionLocal() as media_db_session:
                                page_obj = media_db_session.query(Page).get(page_id)
                                if page_obj:
                                    downloaded_count = self._extract_all_media_files_parallel(page_obj, soup,
                                                                                              media_db_session)
                                    logger.info(f"📊 Downloaded {downloaded_count} media files")
                                else:
                                    logger.error(f"❌ Could not retrieve page object for media extraction")
                        except Exception as media_error:
                            logger.warning(f"⚠ Error processing media files for {url}: {media_error}")

                    # Extract links for further crawling
                    if depth < CRAWL_DEPTH:
                        try:
                            links = self._extract_links_from_page(soup, url)
                            for link_url in links[:5]:  # Limit links per page
                                if link_url != url:  # Avoid self-loops
                                    self._crawl_page_with_fallback_parallel(site, link_url, depth + 1)
                        except Exception as links_error:
                            logger.warning(f"⚠ Error extracting links for {url}: {links_error}")

                    return True
                else:
                    logger.warning(f"⚠ HTTP {response.status_code} for {url} via {proxy_type}")
                    return False
            except Exception as e:
                logger.error(f"❌ Error crawling {url} with {proxy_type}: {e}")
                return False
        except Exception as e:
            logger.error(f"❌ Critical error in _crawl_page_with_fallback_parallel: {e}")
            return False

    def _only_restart_if_truly_broken(self):
        """Only restart I2P if it's genuinely broken, not just slow"""

        # First, be very patient
        if self._patient_i2p_wait(max_minutes=10):
            logger.info("I2P became ready with patience - no restart needed")
            return True

        # Check if process is actually dead
        if not self._is_i2p_process_running():
            logger.warning("I2P process is dead - restart needed")
            return self._gentle_i2p_restart()

        # Process is running but not responding - maybe it's stuck
        logger.info("I2P process running but not responsive after patient wait")

        # Check how long it's been running
        try:
            import subprocess
            result = subprocess.run(
                ["ps", "-o", "etime=", "-p", "$(pgrep -f i2pd)"],
                shell=True, capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                uptime = result.stdout.strip()
                logger.info(f"I2P has been running for: {uptime}")

                # If it's been running for over 20 minutes and still not working, consider restart
                if ":" in uptime:
                    time_parts = uptime.split(":")
                    if len(time_parts) >= 2:
                        minutes = int(time_parts[-2]) if time_parts[-2].isdigit() else 0
                        if len(time_parts) >= 3:  # Hours present
                            minutes += int(time_parts[-3]) * 60 if time_parts[-3].isdigit() else 0

                        if minutes > 20:
                            logger.warning(f"I2P running for {minutes} minutes but not working - gentle restart")
                            return self._gentle_i2p_restart()
        except:
            pass

        logger.info("I2P seems to be working on bootstrap - will continue with limited functionality")
        return False

    def _gentle_i2p_restart(self):
        """Gentle restart that preserves I2P data"""
        logger.info("Performing gentle I2P restart...")

        try:
            import subprocess

            # Gentle shutdown - give I2P time to save state
            logger.info("Sending gentle shutdown signal to I2P...")
            subprocess.run(["pkill", "-TERM", "-f", "i2pd"], timeout=10)

            # Wait for graceful shutdown
            for i in range(15):
                if not self._is_i2p_process_running():
                    logger.info(f"I2P shutdown gracefully after {i + 1} seconds")
                    break
                time.sleep(1)
            else:
                # Force if needed
                logger.warning("Forcing I2P shutdown...")
                subprocess.run(["pkill", "-KILL", "-f", "i2pd"], timeout=5)
                time.sleep(2)

            # Don't delete data - just restart
            logger.info("Restarting I2P with existing data...")
            subprocess.Popen([
                "i2pd",
                "--conf=/etc/i2pd/i2pd.conf",
                "--datadir=/var/lib/i2pd"
            ])

            logger.info("I2P restarted - waiting for it to initialize...")
            time.sleep(15)  # Give it time to start

            return self._patient_i2p_wait(max_minutes=8)

        except Exception as e:
            logger.error(f"Gentle restart failed: {e}")
            return False

def main():
    """Main execution function"""
    logger.info("=" * 50)
    logger.info("AI Research Crawler Starting Up")
    logger.info("=" * 50)

    crawler = None
    try:
        # Initialize crawler
        crawler = AIResearchCrawler()

        # Setup enhanced database
        logger.info("Setting up enhanced database")
        crawler.setup_enhanced_database()

        # Main crawling loop
        while True:
            try:
                logger.info("Starting crawling cycle")

                # Crawl sites
                crawler.crawl_sites()

                # AI analysis cycle
                logger.info("Starting AI analysis cycle")
                crawler.process_pages_for_research()

                # Generate reports
                logger.info("Generating research reports")
                crawler.research_reporting()

                # Sleep until next cycle
                logger.info(f"Cycle completed. Sleeping for {RESEARCH_FREQUENCY_HOURS} hours")
                time.sleep(RESEARCH_FREQUENCY_HOURS * 3600)

            except KeyboardInterrupt:
                logger.info("Received shutdown signal")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(300)  # Wait 5 minutes before retrying

    except Exception as e:
        logger.critical(f"Critical error during initialization: {e}")
        raise
    finally:
        if crawler:
            logger.info("Closing crawler resources")
            crawler.close()

        logger.info("AI Research Crawler shutdown complete")


if __name__ == "__main__":
    main()