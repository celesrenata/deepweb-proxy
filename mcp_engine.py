import os
import time
import requests
from bs4 import BeautifulSoup
import logging
from urllib.parse import urljoin, urlparse
from datetime import datetime, timedelta
import getpass
import sys
import sqlalchemy
import pymysql
import db_models
from urllib.robotparser import RobotFileParser
import mimetypes

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Import database models - will be imported after DB credentials are confirmed
db_models = None

# SOCKS proxy for TOR
TOR_SOCKS = "socks5h://127.0.0.1:9050"
# HTTP proxy for I2P
I2P_PROXY = {"http": "http://127.0.0.1:4444", "https": "http://127.0.0.1:4444"}
OLLAMA_ENDPOINT = os.getenv("OLLAMA_ENDPOINT", "http://10.1.1.12:2701")
SITES_FILE = "/mnt/config/sites.txt"
# For development/testing, use a local file if the mount doesn't exist
if not os.path.exists(os.path.dirname(SITES_FILE)):
    SITES_FILE = "sites.txt"

# Maximum depth for crawling (0 = just the initial page)
CRAWL_DEPTH = int(os.getenv("CRAWL_DEPTH", "1"))
# Number of pages to crawl per site
MAX_PAGES_PER_SITE = int(os.getenv("MAX_PAGES_PER_SITE", "10"))
# Maximum media files per page
MAX_MEDIA_PER_PAGE = int(os.getenv("MAX_MEDIA_PER_PAGE", "20"))
# Maximum size of media files to download (in bytes)
MAX_MEDIA_SIZE = int(os.getenv("MAX_MEDIA_SIZE", "10000000"))  # 10MB default
# Crawl frequency in hours
CRAWL_FREQUENCY_HOURS = int(os.getenv("CRAWL_FREQUENCY_HOURS", "24"))

# MySQL Connection details
DB_HOST = os.getenv("MYSQL_HOST", "10.1.1.12")
DB_PORT = os.getenv("MYSQL_PORT", "3306")
DB_USER = os.getenv("MYSQL_USER", "splinter-research")
DB_PASS = os.getenv("MYSQL_PASSWORD", "PSCh4ng3me!")
DB_NAME = os.getenv("MYSQL_DATABASE", "splinter-research")


def setup_database():
    """Set up database connection and import models"""
    global db_models

    # Configure environment variables for database connection
    os.environ["MYSQL_HOST"] = DB_HOST
    os.environ["MYSQL_PORT"] = DB_PORT
    os.environ["MYSQL_USER"] = DB_USER
    os.environ["MYSQL_PASSWORD"] = DB_PASS
    os.environ["MYSQL_DATABASE"] = DB_NAME

    # Now import database models
    try:
        import db_models as db_models_module
        db_models = db_models_module
        logger.info(f"Database models imported successfully")
        return True
    except ImportError as e:
        logger.error(f"Failed to import database models: {e}")
        return False


def fetch_sites():
    """Get sites from the sites file and ensure they're in the database"""
    try:
        sites = []
        if os.path.exists(SITES_FILE):
            with open(SITES_FILE, "r") as f:
                sites = [line.strip() for line in f if line.strip()]
        else:
            logger.warning(f"Sites file {SITES_FILE} does not exist")

        # Update sites in the database
        session = db_models.get_db_session()
        try:
            # Add new sites from file
            for site_url in sites:
                existing_site = session.query(db_models.Site).filter(db_models.Site.url == site_url).first()
                if not existing_site:
                    new_site = db_models.Site(
                        url=site_url,
                        is_onion=".onion" in site_url,
                        is_i2p=".i2p" in site_url
                    )
                    session.add(new_site)
                    logger.info(f"Added new site to database: {site_url}")

            # Save changes
            session.commit()

            # Return sites that need crawling
            cutoff_time = datetime.utcnow() - timedelta(hours=CRAWL_FREQUENCY_HOURS)
            sites_to_crawl = session.query(db_models.Site).filter(
                (db_models.Site.last_crawled == None) | (db_models.Site.last_crawled <= cutoff_time)
            ).all()

            logger.info(f"Found {len(sites_to_crawl)} sites that need crawling")
            return sites_to_crawl

        except Exception as e:
            session.rollback()
            logger.error(f"Database error in fetch_sites: {e}")
            return []
        finally:
            session.close()

    except Exception as e:
        logger.error(f"Error fetching sites: {e}")
        return []


def get_proxies_for_url(url):
    """Get the appropriate proxy settings for a URL based on its TLD"""
    if ".onion" in url:
        logger.debug(f"Using Tor proxy for {url}")
        return {"http": TOR_SOCKS, "https": TOR_SOCKS}
    elif ".i2p" in url:
        logger.debug(f"Using I2P proxy for {url}")
        return I2P_PROXY
    else:
        logger.debug(f"Using direct connection for {url}")
        return None  # Direct connection


def extract_base_url(url):
    """Extract the base domain from a URL"""
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    return base


def is_allowed_by_robots(url):
    """Check if URL is allowed by robots.txt"""
    try:
        parsed_url = urlparse(url)
        base = f"{parsed_url.scheme}://{parsed_url.netloc}"
        robots_url = f"{base}/robots.txt"

        # Use caching to avoid repeatedly fetching robots.txt
        rp = RobotFileParser()
        rp.set_url(robots_url)

        # Try to fetch robots.txt with appropriate proxies
        proxies = get_proxies_for_url(robots_url)
        try:
            resp = requests.get(robots_url, proxies=proxies, timeout=10)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            else:
                # No valid robots.txt, assume everything is allowed
                return True
        except Exception:
            # Error fetching robots.txt, assume allowed
            return True

        return rp.can_fetch("DeepWebProxyCrawler", url)
    except Exception as e:
        logger.warning(f"Error checking robots.txt for {url}: {e}")
        # If there's any error processing robots.txt, assume it's allowed
        return True


def get_file_type_from_url(url):
    """Determine file type from URL"""
    # Use mimetypes to guess file type
    mime_type, _ = mimetypes.guess_type(url)
    if mime_type:
        main_type, sub_type = mime_type.split('/')
        return main_type

    # Check extension if mimetypes didn't work
    file_ext = os.path.splitext(urlparse(url).path)[1].lower()
    if file_ext:
        if file_ext in ['.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp', '.bmp']:
            return 'image'
        elif file_ext in ['.mp4', '.avi', '.mov', '.webm', '.mkv']:
            return 'video'
        elif file_ext in ['.mp3', '.wav', '.ogg', '.flac']:
            return 'audio'
        elif file_ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']:
            return 'document'
        elif file_ext in ['.zip', '.rar', '.tar', '.gz', '.7z']:
            return 'archive'

    # Default
    return 'unknown'


def crawl_site(site):
    """Crawl a site and store pages and media files in the database"""
    try:
        session = db_models.get_db_session()
        try:
            logger.info(f"Crawling site: {site.url}")
            visited_urls = set()
            urls_to_visit = [(site.url, 0, 0)]  # (url, depth, priority_score)
            media_urls = set()  # Track media URLs to avoid duplicates

            # Parse the base domain
            parsed_base = urlparse(site.url)
            base_domain = parsed_base.netloc

            page_count = 0

            while urls_to_visit and page_count < MAX_PAGES_PER_SITE:
                current_url, depth, _ = urls_to_visit.pop(0)

                if current_url in visited_urls:
                    continue

                visited_urls.add(current_url)

                # Check robots.txt
                if not is_allowed_by_robots(current_url):
                    logger.info(f"Skipping {current_url} - disallowed by robots.txt")
                    continue

                # Crawl the page
                try:
                    proxies = get_proxies_for_url(current_url)
                    logger.info(f"Fetching {current_url} with proxies: {proxies}")

                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                    }

                    resp = requests.get(
                        current_url,
                        proxies=proxies,
                        timeout=30,
                        headers=headers
                    )
                    resp.raise_for_status()

                    # Parse content
                    soup = BeautifulSoup(resp.text, "html.parser")
                    title = soup.title.string if soup.title else "No title"
                    text_content = soup.get_text(separator=" ")

                    logger.info(f"Successfully crawled {current_url}")
                    logger.info(f"Title: {title[:50]}..." if len(title or '') > 50 else f"Title: {title}")

                    # Limit content size to prevent database errors
                    if len(text_content) > 15000000:  # ~15MB, below MEDIUMTEXT limit
                        logger.warning(f"Text content too large ({len(text_content)} bytes), truncating")
                        text_content = text_content[:15000000]

                    html_content = resp.text
                    if len(html_content) > 15000000:  # ~15MB, below MEDIUMTEXT limit
                        logger.warning(f"HTML content too large ({len(html_content)} bytes), truncating")
                        html_content = html_content[:15000000]

                    # Store in database
                    existing_page = session.query(db_models.Page).filter(
                        db_models.Page.site_id == site.id,
                        db_models.Page.url == current_url
                    ).first()

                    page_id = None
                    if existing_page:
                        # Update existing page
                        existing_page.title = title
                        existing_page.content_text = text_content
                        existing_page.html_content = html_content
                        existing_page.crawled_at = datetime.utcnow()
                        page_id = existing_page.id
                        logger.info(f"Updated existing page: {current_url}")
                    else:
                        # Create new page
                        new_page = db_models.Page(
                            site_id=site.id,
                            url=current_url,
                            title=title,
                            content_text=text_content,
                            html_content=html_content,
                            crawled_at=datetime.utcnow()
                        )
                        session.add(new_page)
                        session.flush()  # Flush to get the ID without committing
                        page_id = new_page.id
                        logger.info(f"Added new page: {current_url}")

                    # Save changes so far
                    session.commit()
                    page_count += 1
                    logger.info(f"Stored page {page_count}/{MAX_PAGES_PER_SITE}: {current_url}")

                    # Extract and process media files
                    media_tags = [
                        (soup.find_all('img', src=True), 'image'),
                        (soup.find_all('video', src=True), 'video'),
                        (soup.find_all('audio', src=True), 'audio'),
                        (soup.find_all('source', src=True), 'source'),
                        (soup.find_all('a', href=True), 'link')
                    ]

                    media_count = 0
                    for tags, tag_type in media_tags:
                        for tag in tags:
                            # Stop if we've processed enough media files
                            if media_count >= MAX_MEDIA_PER_PAGE:
                                logger.info(f"Reached maximum media files per page ({MAX_MEDIA_PER_PAGE})")
                                break

                            # Get URL attribute based on tag type
                            if tag_type == 'link':
                                url_attr = tag['href']
                                # Only process links to media files
                                media_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.pdf', '.doc',
                                                    '.zip', '.mp3', '.mp4', '.avi', '.mov', '.svg']
                                if not any(url_attr.lower().endswith(ext) for ext in media_extensions):
                                    continue
                            else:
                                url_attr = tag['src']

                            # Make absolute URL
                            media_url = urljoin(current_url, url_attr)

                            # Skip already processed media
                            if media_url in media_urls:
                                continue

                            media_urls.add(media_url)

                            # Check if media is from same domain
                            parsed_media = urlparse(media_url)
                            if parsed_media.netloc and parsed_media.netloc != base_domain:
                                # For images, download from external domains too
                                if tag_type != 'image':
                                    continue  # Skip external non-image media

                            # Determine file type
                            file_type = get_file_type_from_url(media_url)

                            # Get filename from URL
                            filename = os.path.basename(parsed_media.path)
                            if not filename or filename == '':
                                filename = f"{file_type}_{hash(media_url) % 10000}{os.path.splitext(parsed_media.path)[1]}"
                                if not os.path.splitext(filename)[1]:
                                    # Add default extension if none exists
                                    extensions = {
                                        'image': '.jpg', 'video': '.mp4', 'audio': '.mp3',
                                        'document': '.pdf', 'archive': '.zip', 'unknown': '.bin'
                                    }
                                    filename += extensions.get(file_type, '.bin')

                            # Download media file
                            try:
                                logger.info(f"Downloading media: {media_url}")
                                media_resp = requests.get(
                                    media_url,
                                    proxies=proxies,
                                    timeout=30,
                                    headers=headers,
                                    stream=True  # Stream to handle large files
                                )
                                media_resp.raise_for_status()

                                # Get content size from headers or response
                                content_length = int(media_resp.headers.get('content-length', 0))
                                if content_length == 0:  # If header not provided
                                    media_content = media_resp.content
                                    content_length = len(media_content)
                                else:
                                    # Skip very large files
                                    if content_length > MAX_MEDIA_SIZE:
                                        logger.warning(f"Media file too large: {content_length} bytes, skipping")
                                        continue
                                    media_content = media_resp.content

                                # Check if content is too large for the database
                                if len(media_content) > 15000000:  # 15MB limit
                                    logger.warning(
                                        f"Media content too large for database ({len(media_content)} bytes), truncating")
                                    continue  # Skip this file

                                # Store the media file
                                new_media = db_models.MediaFile(
                                    page_id=page_id,
                                    url=media_url,
                                    file_type=file_type,
                                    content=media_content,
                                    size_bytes=len(media_content),
                                    filename=filename,
                                    downloaded_at=datetime.utcnow()
                                )
                                session.add(new_media)
                                media_count += 1
                                logger.info(f"Stored media file: {filename} ({file_type}, {len(media_content)} bytes)")

                                # Commit media in batches to avoid long transactions
                                if media_count % 5 == 0:
                                    session.commit()

                            except Exception as e:
                                logger.error(f"Error downloading media {media_url}: {e}")
                                continue

                    # Save changes for media files
                    if media_count > 0:
                        session.commit()
                        logger.info(f"Stored {media_count} media files for page: {current_url}")

                    # Extract links for further crawling if not at max depth
                    if depth < CRAWL_DEPTH:
                        links = soup.find_all('a', href=True)
                        new_urls = 0

                        for link in links:
                            href = link['href']

                            # Skip empty links, anchors, javascript, or mailto
                            if (not href or href.startswith('#') or href.startswith('javascript:')
                                    or href.startswith('mailto:') or href.startswith('tel:')):
                                continue

                            # Make absolute URL
                            absolute_url = urljoin(current_url, href)
                            parsed_url = urlparse(absolute_url)

                            # Skip if not same domain
                            if parsed_url.netloc != base_domain:
                                continue

                            # Skip already visited or media files
                            if absolute_url in visited_urls or absolute_url in media_urls:
                                continue

                            # Skip common media and document extensions that we handle separately
                            media_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.pdf', '.doc',
                                                '.zip', '.mp3', '.mp4', '.avi', '.mov', '.svg']
                            if any(absolute_url.lower().endswith(ext) for ext in media_extensions):
                                # Don't add to crawl queue, but note it for media download
                                media_urls.add(absolute_url)
                                continue

                            # Add to queue with priority based on URL "quality"
                            # - URLs with fewer query parameters get priority
                            # - URLs with fewer path segments get priority
                            priority_score = len(parsed_url.path.split('/')) + len(parsed_url.query)

                            # Insert based on priority (lower score = higher priority)
                            insertion_idx = 0
                            for idx, (_, _, score) in enumerate(urls_to_visit):
                                if score > priority_score:
                                    break
                                insertion_idx = idx + 1

                            urls_to_visit.insert(insertion_idx, (absolute_url, depth + 1, priority_score))
                            new_urls += 1

                        logger.info(f"Found {new_urls} new URLs to crawl at depth {depth + 1}")

                except requests.exceptions.RequestException as e:
                    logger.error(f"Request error crawling {current_url}: {e}")
                    continue
                except Exception as e:
                    logger.error(f"Error crawling {current_url}: {e}")
                    continue

            # Update the site's last crawled timestamp
            site.last_crawled = datetime.utcnow()
            session.commit()

            logger.info(f"Completed crawling site {site.url}, stored {page_count} pages")
            return True

        except Exception as e:
            session.rollback()
            logger.error(f"Database error in crawl_site: {e}")
            return False
        finally:
            session.close()

    except Exception as e:
        logger.error(f"Error in crawl_site: {e}")
        return False


def analyze_content(content):
    """Analyze content using the Ollama LLM API"""
    try:
        # Truncate content if too large
        if len(content) > 8000:
            content = content[:8000]

        payload = {"model": "gemma3:12b", "prompt": content}
        logger.info(f"Sending content to Ollama for analysis")
        r = requests.post(f"{OLLAMA_ENDPOINT}/v1/completions", json=payload, timeout=60)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"Error calling Ollama: {e}")
        return None


def ensure_sites_file():
    """Ensure the sites.txt file exists with at least one site"""
    if not os.path.exists(SITES_FILE):
        logger.info(f"Creating default sites file at {SITES_FILE}")
        directory = os.path.dirname(SITES_FILE)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        with open(SITES_FILE, "w") as f:
            f.write("https://news.ycombinator.com/\n")  # Default site


def reset_database():
    """Drop all tables and recreate them"""
    try:
        logger.info("Dropping all database tables...")
        db_models.Base.metadata.drop_all(db_models.engine)
        logger.info("Recreating all database tables...")
        db_models.Base.metadata.create_all(db_models.engine)
        logger.info("Database reset successfully")
        return True
    except Exception as e:
        logger.error(f"Error resetting database: {e}")
        return False


if __name__ == "__main__":
    logger.info("Starting MCP Engine")

    # Request password if not set in environment
    if not os.getenv("MYSQL_PASSWORD"):
        password = getpass.getpass(f"Enter MySQL password for {DB_USER}@{DB_HOST}: ")
        os.environ["MYSQL_PASSWORD"] = password

    # Setup database connection
    if not setup_database():
        logger.error("Failed to set up database connection, exiting")
        exit(1)

    # Reset the database (uncomment to reset)
    if reset_database():
        logger.info("Database has been reset")

    # Initialize the database
    try:
        if not db_models.init_db():
            logger.error("Failed to initialize database, exiting")
            exit(1)
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        exit(1)

    # Ensure sites.txt file exists
    ensure_sites_file()

    # Main crawl loop
    logger.info("Starting main crawl loop")
    while True:
        try:
            # Fetch sites that need crawling
            sites = fetch_sites()
            logger.info(f"Found {len(sites)} sites to crawl")

            # Crawl each site
            for site in sites:
                crawl_site(site)
                time.sleep(5)  # Small pause between sites to avoid overwhelming resources

            # Sleep before next crawl cycle
            logger.info(f"Crawl cycle completed. Sleeping for 5 minutes before next cycle")
            time.sleep(300)

        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            time.sleep(60)  # Sleep a bit before retrying