import os
import base64
import requests
import logging
import json
from io import BytesIO
from PIL import Image
from datetime import datetime, timedelta
from sqlalchemy import desc, func, and_, distinct
from minio import Minio
from db_models import get_db_session, Page, Site, MediaFile

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration - Updated to match your environment
OLLAMA_ENDPOINT = os.getenv("OLLAMA_API_URL", "http://localhost:11434")
MULTIMODAL_MODEL = os.getenv("MULTIMODAL_MODEL", "llava:latest")
TEXT_MODEL = os.getenv("TEXT_MODEL", "llama3.1:8b")
MAX_IMAGES_PER_PAGE = 5
MAX_IMAGE_SIZE = (800, 800)
EXTENDED_CONTEXT_SIZE = 16384

# MinIO Configuration - Match your setup
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio-crawler-hl.minio-service:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() in ('true', '1', 'yes', 'on')
MINIO_BUCKET_IMAGES = os.getenv("MINIO_BUCKET_IMAGES", "crawler-images")
MINIO_BUCKET_AUDIO = os.getenv("MINIO_BUCKET_AUDIO", "crawler-audio")
MINIO_BUCKET_VIDEO = os.getenv("MINIO_BUCKET_VIDEO", "crawler-videos")
MINIO_BUCKET_OTHER = os.getenv("MINIO_BUCKET_OTHER", "crawler-media")


def setup_minio_client():
    """Setup MinIO client for retrieving media files."""
    try:
        if not MINIO_ENDPOINT or not MINIO_ACCESS_KEY or not MINIO_SECRET_KEY:
            logger.warning("MinIO configuration incomplete")
            return None

        # Clean the endpoint
        endpoint = MINIO_ENDPOINT
        if endpoint.startswith('http://'):
            endpoint = endpoint[7:]
        elif endpoint.startswith('https://'):
            endpoint = endpoint[8:]

        client = Minio(
            endpoint=endpoint,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE
        )

        # Test connection
        client.list_buckets()
        logger.info("✓ MinIO client setup successful")
        return client

    except Exception as e:
        logger.error(f"✗ MinIO client setup failed: {e}")
        return None


def get_media_from_minio(media_file):
    """Retrieve media file content from MinIO."""
    minio_client = setup_minio_client()
    if not minio_client:
        return None

    try:
        # Determine bucket based on media type
        bucket_name = get_bucket_for_media_type(media_file.file_type)

        # Get object from MinIO
        if media_file.minio_object_name:
            response = minio_client.get_object(bucket_name, media_file.minio_object_name)
            content = response.read()
            response.close()
            return content
        else:
            logger.warning(f"No MinIO object name for media file {media_file.id}")
            return None

    except Exception as e:
        logger.error(f"Error retrieving media from MinIO: {e}")
        return None


def get_bucket_for_media_type(file_type):
    """Determine MinIO bucket based on file type."""
    if not file_type:
        return MINIO_BUCKET_OTHER

    file_type_lower = file_type.lower()

    if any(img_type in file_type_lower for img_type in
           ['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/bmp']):
        return MINIO_BUCKET_IMAGES
    elif any(audio_type in file_type_lower for audio_type in ['audio/', 'application/ogg']):
        return MINIO_BUCKET_AUDIO
    elif any(video_type in file_type_lower for video_type in ['video/', 'application/mp4']):
        return MINIO_BUCKET_VIDEO
    else:
        return MINIO_BUCKET_OTHER


def get_page_with_media(page_id):
    """Get a page with its associated media files from MinIO."""
    session = get_db_session()
    try:
        page = session.query(Page).filter(Page.id == page_id).first()
        if not page:
            return None, []

        # Fetch associated media files from database
        media_files = session.query(MediaFile).filter(
            MediaFile.page_id == page_id,
            MediaFile.minio_object_name != None  # Only get files stored in MinIO
        ).limit(MAX_IMAGES_PER_PAGE).all()

        # Download content from MinIO for each media file
        media_with_content = []
        for media in media_files:
            content = get_media_from_minio(media)
            if content:
                # Create a copy with content
                media_copy = type('MediaFile', (), {
                    'id': media.id,
                    'filename': media.filename,
                    'file_type': media.file_type,
                    'size_bytes': media.size_bytes,
                    'minio_object_name': media.minio_object_name,
                    'content': content
                })()
                media_with_content.append(media_copy)

        return page, media_with_content

    except Exception as e:
        logger.error(f"Error retrieving page with media: {e}")
        return None, []
    finally:
        session.close()


def process_with_multimodal_ai(text, images=None, prompt=None, extended_context=True):
    """Process text and images with a multimodal AI model using Ollama API."""
    if not text and not images:
        return "No content provided for analysis."

    if not prompt:
        prompt = """Analyze this content comprehensively and provide a detailed analysis with the following structure:

1. **Executive Summary** (2-3 paragraphs): Provide a comprehensive overview of the content, its main themes, and overall significance.

2. **Detailed Content Analysis**:
   - Primary topics and themes discussed
   - Communication patterns and user behavior
   - Technical aspects (if applicable)
   - Community dynamics and interactions

3. **Contextual Information**:
   - Platform/site characteristics
   - User demographics (if discernible)
   - Cultural or regional indicators
   - Temporal patterns (timing, frequency)

4. **Security and Risk Assessment**:
   - Potential security concerns
   - Privacy implications
   - Legal considerations
   - Ethical issues

5. **Intelligence Value**:
   - Information that could be useful for research
   - Patterns or trends identified
   - Connections to other content or sites
   - Predictive indicators

6. **Recommendations**:
   - Further investigation priorities
   - Monitoring recommendations
   - Risk mitigation strategies

Please provide specific examples and quotes where relevant, and maintain a professional, analytical tone throughout."""

    try:
        # For Ollama's generate endpoint
        api_url = f"{OLLAMA_ENDPOINT.rstrip('/')}/api/generate"

        # Prepare the prompt with text content
        full_prompt = f"{prompt}\n\nContent to analyze:\n{text}"

        # If we have images, add them to the prompt
        if images and len(images) > 0:
            full_prompt += f"\n\nThis content includes {len(images)} image(s) for visual analysis."
            # Note: Image handling may need adjustment based on your Ollama setup
            # Some Ollama installations support base64 images in the prompt

        response = requests.post(
            api_url,
            json={
                "model": MULTIMODAL_MODEL,
                "prompt": full_prompt,
                "stream": False,
                "options": {
                    "num_ctx": EXTENDED_CONTEXT_SIZE if extended_context else 8192,
                    "temperature": 0.3,
                    "top_p": 0.9,
                    "repeat_penalty": 1.1
                }
            },
            timeout=300
        )

        if response.status_code == 200:
            result = response.json()
            return result.get("response", "No response from model")
        else:
            logger.error(f"API error: {response.status_code} - {response.text}")
            return f"Error: Failed to get response from AI model. Status code: {response.status_code}"

    except Exception as e:
        logger.error(f"Error communicating with multimodal AI model: {e}")
        return f"Error: {str(e)}"


def process_with_text_ai(text, prompt=None, extended_context=True):
    """Process text with a text-only AI model using Ollama API."""
    if not text:
        return "No text provided for analysis."

    if not prompt:
        prompt = """Analyze this content comprehensively and provide a detailed analysis with the following structure:

1. **Executive Summary** (2-3 paragraphs): Provide a comprehensive overview of the content, its main themes, and overall significance.

2. **Detailed Content Analysis**:
   - Primary topics and themes discussed
   - Communication patterns and user behavior
   - Technical aspects (if applicable)
   - Community dynamics and interactions

3. **Contextual Information**:
   - Platform/site characteristics
   - User demographics (if discernible)
   - Cultural or regional indicators
   - Temporal patterns (timing, frequency)

4. **Security and Risk Assessment**:
   - Potential security concerns
   - Privacy implications
   - Legal considerations
   - Ethical issues

5. **Intelligence Value**:
   - Information that could be useful for research
   - Patterns or trends identified
   - Connections to other content or sites
   - Predictive indicators

6. **Recommendations**:
   - Further investigation priorities
   - Monitoring recommendations
   - Risk mitigation strategies

Please provide specific examples and quotes where relevant, and maintain a professional, analytical tone throughout."""

    full_prompt = f"{prompt}\n\nContent to analyze:\n{text}"

    try:
        api_url = f"{OLLAMA_ENDPOINT.rstrip('/')}/api/generate"

        response = requests.post(
            api_url,
            json={
                "model": TEXT_MODEL,
                "prompt": full_prompt,
                "stream": False,
                "options": {
                    "num_ctx": EXTENDED_CONTEXT_SIZE if extended_context else 8192,
                    "temperature": 0.3,
                    "top_p": 0.9,
                    "repeat_penalty": 1.1
                }
            },
            timeout=300
        )

        if response.status_code == 200:
            result = response.json()
            return result.get("response", "No response from model")
        else:
            logger.error(f"API error: {response.status_code} - {response.text}")
            return f"Error: Failed to get response from AI model. Status code: {response.status_code}"

    except Exception as e:
        logger.error(f"Error communicating with AI model: {e}")
        return f"Error: {str(e)}"

def get_all_sites():
    """Retrieve all sites from the database."""
    session = get_db_session()
    try:
        sites = session.query(Site).all()
        return sites
    except Exception as e:
        logger.error(f"Error retrieving sites: {e}")
        return []
    finally:
        session.close()


def get_pages_by_site(site_id, limit=None):
    """Get all pages for a specific site."""
    session = get_db_session()
    try:
        query = session.query(Page).filter(Page.site_id == site_id).order_by(desc(Page.crawled_at))
        if limit:
            query = query.limit(limit)
        pages = query.all()
        return pages
    except Exception as e:
        logger.error(f"Error retrieving pages for site {site_id}: {e}")
        return []
    finally:
        session.close()


def get_site_statistics(site_id):
    """Get statistics for a specific site."""
    session = get_db_session()
    try:
        site = session.query(Site).filter(Site.id == site_id).first()
        if not site:
            return None

        page_count = session.query(func.count(Page.id)).filter(Page.site_id == site_id).scalar()
        media_count = session.query(func.count(MediaFile.id)).join(Page).filter(Page.site_id == site_id).scalar()

        # Get date range of crawled content
        date_range = session.query(
            func.min(Page.crawled_at).label('first_crawl'),
            func.max(Page.crawled_at).label('last_crawl')
        ).filter(Page.site_id == site_id).first()

        # Get unique page types/sections
        unique_paths = session.query(distinct(Page.url)).filter(Page.site_id == site_id).count()

        return {
            'site': site,
            'page_count': page_count,
            'media_count': media_count,
            'unique_paths': unique_paths,
            'first_crawl': date_range.first_crawl,
            'last_crawl': date_range.last_crawl
        }
    except Exception as e:
        logger.error(f"Error getting site statistics: {e}")
        return None
    finally:
        session.close()


def get_recent_pages(limit=10, days_back=7):
    """Retrieve the most recently crawled pages from the database within a time range."""
    session = get_db_session()
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=days_back)
        pages = session.query(Page).filter(
            Page.crawled_at >= cutoff_date
        ).order_by(desc(Page.crawled_at)).limit(limit).all()
        return pages
    except Exception as e:
        logger.error(f"Error retrieving recent pages: {e}")
        return []
    finally:
        session.close()


def resize_image(image_data, max_size=MAX_IMAGE_SIZE):
    """Resize an image to the maximum size while preserving aspect ratio."""
    try:
        image = Image.open(BytesIO(image_data))
        image.thumbnail(max_size)
        buffer = BytesIO()
        # Save as JPEG for better compression
        if image.mode in ("RGBA", "P"):
            image = image.convert("RGB")
        image.save(buffer, format="JPEG", quality=85)
        return buffer.getvalue()
    except Exception as e:
        logger.error(f"Error resizing image: {e}")
        return image_data


def image_to_base64(image_data):
    """Convert image data to base64 string."""
    try:
        # Resize large images
        resized_image = resize_image(image_data)
        return base64.b64encode(resized_image).decode('utf-8')
    except Exception as e:
        logger.error(f"Error converting image to base64: {e}")
        return None


def is_image_file(file_type):
    """Check if the file type is an image."""
    image_types = ['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/bmp']
    return file_type and any(img_type in file_type.lower() for img_type in image_types)

def analyze_site_comprehensively(site_id, max_pages=None):
    """Perform comprehensive analysis of an entire site."""
    session = get_db_session()
    try:
        site = session.query(Site).filter(Site.id == site_id).first()
        if not site:
            logger.error(f"Site not found with ID: {site_id}")
            return None

        logger.info(f"Starting comprehensive analysis of site: {site.url}")

        # Get site statistics
        stats = get_site_statistics(site_id)

        # Get all pages for the site
        pages = get_pages_by_site(site_id, limit=max_pages)

        if not pages:
            logger.warning(f"No pages found for site: {site.url}")
            return None

        # Analyze each page
        page_analyses = []
        for i, page in enumerate(pages, 1):
            logger.info(f"Analyzing page {i}/{len(pages)}: {page.url}")
            result = analyze_page_with_media(page.id, extended_context=True)
            if result:
                page_analyses.append(result)

        # Generate site-wide summary
        site_summary = generate_site_summary(site, stats, page_analyses)

        return {
            "site": site,
            "statistics": stats,
            "page_analyses": page_analyses,
            "site_summary": site_summary,
            "analysis_timestamp": datetime.utcnow()
        }

    except Exception as e:
        logger.error(f"Error in comprehensive site analysis: {e}")
        return None
    finally:
        session.close()


def generate_site_summary(site, stats, page_analyses):
    """Generate a comprehensive summary of the entire site."""
    if not page_analyses:
        return "No page analyses available for summary generation."

    # Combine all page content for site-wide analysis
    combined_content = f"""
Site Analysis Summary for: {site.url}
Site Type: {'Onion Service' if site.is_onion else 'I2P Site' if site.is_i2p else 'Clear Web'}
Total Pages Analyzed: {len(page_analyses)}
Total Pages on Site: {stats['page_count'] if stats else 'Unknown'}
Total Media Files: {stats['media_count'] if stats else 'Unknown'}

Individual Page Analyses:
"""

    for analysis in page_analyses:
        combined_content += f"\n--- Page: {analysis['url']} ---\n"
        combined_content += f"Title: {analysis['title']}\n"
        combined_content += f"Analysis: {analysis['analysis'][:1000]}...\n"  # Truncate for summary

    # Generate site-wide summary
    summary_prompt = """Based on the individual page analyses provided, create a comprehensive site-wide intelligence summary that includes:

1. **Site Overview**: Overall purpose, nature, and characteristics of the site
2. **Content Patterns**: Common themes, topics, and types of content across pages
3. **User Base and Community**: Analysis of user behavior, demographics, and community dynamics
4. **Technical Infrastructure**: Observations about the site's technical setup and security measures
5. **Risk Assessment**: Overall security, legal, and ethical concerns for the entire site
6. **Intelligence Value**: Key insights and intelligence value of monitoring this site
7. **Strategic Recommendations**: Long-term monitoring and investigation strategies

Focus on patterns, trends, and insights that emerge when viewing the site as a whole rather than individual pages."""

    logger.info("Generating site-wide summary...")
    summary = process_with_text_ai(combined_content, prompt=summary_prompt, extended_context=True)

    return summary


def analyze_all_sites_by_type(site_type="all", max_pages_per_site=10):
    """Analyze all sites of a specific type (onion, i2p, or clearweb)."""
    session = get_db_session()
    try:
        # Filter sites by type
        query = session.query(Site)
        if site_type.lower() == "onion":
            query = query.filter(Site.is_onion == True)
        elif site_type.lower() == "i2p":
            query = query.filter(Site.is_i2p == True)
        elif site_type.lower() == "clearweb":
            query = query.filter(and_(Site.is_onion == False, Site.is_i2p == False))

        sites = query.all()

        if not sites:
            logger.warning(f"No sites found of type: {site_type}")
            return []

        results = []
        for i, site in enumerate(sites, 1):
            logger.info(f"Analyzing site {i}/{len(sites)}: {site.url}")
            result = analyze_site_comprehensively(site.id, max_pages=max_pages_per_site)
            if result:
                results.append(result)

        return results

    except Exception as e:
        logger.error(f"Error analyzing sites by type: {e}")
        return []
    finally:
        session.close()


def save_comprehensive_analysis(results, filename=None, format="txt"):
    """Save comprehensive analysis results to a file."""
    if not results:
        logger.warning("No results to save.")
        return None

    if not filename:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"comprehensive_analysis_{timestamp}.{format}"

    if format.lower() == "json":
        # Convert datetime objects to strings for JSON serialization
        serializable_results = []
        for result in results:
            # Handle both comprehensive site analysis and individual page analysis
            if "site" in result:
                # Comprehensive site analysis
                serializable_result = {
                    "site_url": result['site'].url,
                    "site_type": "onion" if result['site'].is_onion else "i2p" if result['site'].is_i2p else "clearweb",
                    "statistics": {
                        "page_count": result['statistics']['page_count'] if result['statistics'] else 0,
                        "media_count": result['statistics']['media_count'] if result['statistics'] else 0,
                        "unique_paths": result['statistics']['unique_paths'] if result['statistics'] else 0
                    },
                    "page_analyses": [
                        {
                            "url": pa['url'],
                            "title": pa['title'],
                            "crawled_at": pa['crawled_at'].isoformat() if isinstance(pa['crawled_at'],
                                                                                     datetime) else str(
                                pa['crawled_at']),
                            "image_count": pa['image_count'],
                            "analysis": pa['analysis']
                        }
                        for pa in result['page_analyses']
                    ],
                    "site_summary": result['site_summary'],
                    "analysis_timestamp": result['analysis_timestamp'].isoformat()
                }
            else:
                # Individual page analysis - get site info from page analysis
                page_analyses = result.get('page_analyses', [])
                if page_analyses:
                    first_page = page_analyses[0]
                    # Extract site info from the first page's site_info
                    site_info = first_page.get('site_info', 'Site: Unknown')
                    site_url = site_info.split('Site: ')[1].split(' ')[0] if 'Site: ' in site_info else 'Unknown'
                    is_onion = 'Onion: True' in site_info
                    is_i2p = 'I2P: True' in site_info

                    serializable_result = {
                        "site_url": site_url,
                        "site_type": "onion" if is_onion else "i2p" if is_i2p else "clearweb",
                        "statistics": {
                            "page_count": len(page_analyses),
                            "media_count": sum(pa.get('media_count', 0) for pa in page_analyses),
                            "unique_paths": len(set(pa['url'] for pa in page_analyses))
                        },
                        "page_analyses": [
                            {
                                "url": pa['url'],
                                "title": pa['title'],
                                "crawled_at": pa['crawled_at'].isoformat() if isinstance(pa['crawled_at'],
                                                                                         datetime) else str(
                                    pa['crawled_at']),
                                "image_count": pa['image_count'],
                                "analysis": pa['analysis']
                            }
                            for pa in page_analyses
                        ],
                        "site_summary": "Individual page analysis - no site summary available",
                        "analysis_timestamp": datetime.now().isoformat()
                    }
                else:
                    # Fallback for empty results
                    serializable_result = {
                        "site_url": "Unknown",
                        "site_type": "unknown",
                        "statistics": {"page_count": 0, "media_count": 0, "unique_paths": 0},
                        "page_analyses": [],
                        "site_summary": "No data available",
                        "analysis_timestamp": datetime.now().isoformat()
                    }

            serializable_results.append(serializable_result)

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(serializable_results, f, indent=2)
    else:
        # Enhanced text format
        with open(filename, "w", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write("COMPREHENSIVE SITE ANALYSIS REPORT\n")
            f.write("=" * 80 + "\n\n")

            for i, result in enumerate(results, 1):
                # Handle both comprehensive site analysis and individual page analysis
                if "site" in result:
                    # Comprehensive site analysis
                    f.write(f"SITE {i}: {result['site'].url}\n")
                    f.write("-" * 60 + "\n")
                    f.write(
                        f"Type: {'Onion Service' if result['site'].is_onion else 'I2P Site' if result['site'].is_i2p else 'Clear Web'}\n")

                    if result['statistics']:
                        stats = result['statistics']
                        f.write(f"Pages: {stats['page_count']}\n")
                        f.write(f"Media Files: {stats['media_count']}\n")
                        f.write(f"Unique Paths: {stats['unique_paths']}\n")
                        f.write(f"First Crawl: {stats['first_crawl']}\n")
                        f.write(f"Last Crawl: {stats['last_crawl']}\n")

                    f.write(f"\nSITE-WIDE SUMMARY:\n")
                    f.write(f"{result['site_summary']}\n\n")

                    page_analyses = result['page_analyses']
                else:
                    # Individual page analysis
                    page_analyses = result.get('page_analyses', [])
                    if page_analyses:
                        first_page = page_analyses[0]
                        site_info = first_page.get('site_info', 'Site: Unknown')
                        site_url = site_info.split('Site: ')[1].split(' ')[0] if 'Site: ' in site_info else 'Unknown'
                        is_onion = 'Onion: True' in site_info
                        is_i2p = 'I2P: True' in site_info

                        f.write(f"ANALYSIS {i}: {site_url}\n")
                        f.write("-" * 60 + "\n")
                        f.write(f"Type: {'Onion Service' if is_onion else 'I2P Site' if is_i2p else 'Clear Web'}\n")
                        f.write(f"Pages Analyzed: {len(page_analyses)}\n")
                        f.write(f"Total Media Files: {sum(pa.get('media_count', 0) for pa in page_analyses)}\n")
                        f.write(f"Total Images: {sum(pa.get('image_count', 0) for pa in page_analyses)}\n\n")
                        f.write(
                            f"SITE-WIDE SUMMARY:\nIndividual page analysis - no comprehensive site summary available\n\n")
                    else:
                        f.write(f"ANALYSIS {i}: No data available\n")
                        f.write("-" * 60 + "\n")
                        page_analyses = []

                f.write(f"INDIVIDUAL PAGE ANALYSES:\n")
                f.write("-" * 40 + "\n")

                for pa in page_analyses:
                    f.write(f"\nURL: {pa['url']}\n")
                    f.write(f"Title: {pa['title']}\n")
                    f.write(f"Crawled: {pa['crawled_at']}\n")
                    f.write(f"Images: {pa['image_count']}, Media: {pa.get('media_count', 0)}\n")
                    f.write(f"Analysis:\n{pa['analysis']}\n")
                    f.write("-" * 40 + "\n")

                f.write("\n" + "=" * 80 + "\n\n")

    logger.info(f"Comprehensive analysis results saved to {filename}")
    return filename

def analyze_page_with_media(page_id, extended_context=True):
    """Analyze a page and its media files using the multimodal AI model with MinIO integration."""
    page, media_files = get_page_with_media(page_id)

    if not page:
        logger.warning(f"Page not found with ID: {page_id}")
        return None

    logger.info(f"Analyzing page with extended context: {page.url}")

    # Get site information for context
    session = get_db_session()
    try:
        site = session.query(Site).filter(Site.id == page.site_id).first()
        site_info = f"Site: {site.url} (Onion: {site.is_onion}, I2P: {site.is_i2p})" if site else "Site: Unknown"
    except:
        site_info = "Site: Unknown"
    finally:
        session.close()

    # Prepare comprehensive text content
    content = f"""
{site_info}
Page URL: {page.url}
Page Title: {page.title}
Crawled At: {page.crawled_at}

Full Content:
{page.content_text}

HTML Structure Context:
{page.html_content[:2000] if page.html_content else "No HTML content available"}...
"""

    # Prepare image content
    images = []
    image_descriptions = []

    for idx, media in enumerate(media_files):
        if is_image_file(media.file_type) and hasattr(media, 'content') and media.content:
            img_base64 = image_to_base64(media.content)
            if img_base64:
                images.append(img_base64)
                image_descriptions.append(
                    f"Image {idx + 1}: {media.filename or 'Unnamed'} ({media.file_type}, {media.size_bytes} bytes)")

    # Add image descriptions to the content
    if image_descriptions:
        content += "\n\nAttached Media Files:\n" + "\n".join(image_descriptions)

    # Choose the appropriate processing method
    if images:
        logger.info(f"Processing page {page.id} with {len(images)} images using multimodal model with extended context")
        analysis = process_with_multimodal_ai(content, images, extended_context=extended_context)
    else:
        logger.info(f"Processing page {page.id} with text-only model with extended context")
        analysis = process_with_text_ai(content, extended_context=extended_context)

    return {
        "page_id": page.id,
        "site_id": page.site_id,
        "url": page.url,
        "title": page.title,
        "crawled_at": page.crawled_at,
        "site_info": site_info,
        "image_count": len(images),
        "media_count": len(media_files),
        "analysis": analysis
    }



if __name__ == "__main__":
    print("Enhanced AI Analysis with Comprehensive Site Analysis")
    print("1. Analyze recent content with extended context")
    print("2. Comprehensive analysis of a specific site")
    print("3. Analyze all sites by type (onion/i2p/clearweb)")
    print("4. Analyze pages with the most media")
    print("5. Search and analyze content containing a keyword")

    choice = input("Choose an option (1-5): ")

    if choice == "1":
        limit = int(input("How many recent pages to analyze? (default: 10): ") or "10")
        days = int(input("Look back how many days? (default: 7): ") or "7")
        pages = get_recent_pages(limit, days)
        results = []
        for page in pages:
            result = analyze_page_with_media(page.id, extended_context=True)
            if result:
                results.append({"page_analyses": [result]})

    elif choice == "2":
        sites = get_all_sites()
        print("\nAvailable sites:")
        for i, site in enumerate(sites, 1):
            print(f"{i}. {site.url} ({'Onion' if site.is_onion else 'I2P' if site.is_i2p else 'Clear Web'})")

        site_choice = int(input("Select site number: ")) - 1
        if 0 <= site_choice < len(sites):
            max_pages = int(input("Maximum pages to analyze (leave blank for all): ") or "0") or None
            result = analyze_site_comprehensively(sites[site_choice].id, max_pages)
            results = [result] if result else []
        else:
            print("Invalid site selection.")
            results = []

    elif choice == "3":
        site_type = input("Site type (onion/i2p/clearweb/all) [default: all]: ").lower() or "all"
        max_pages = int(input("Max pages per site (default: 10): ") or "10")
        results = analyze_all_sites_by_type(site_type, max_pages)

    elif choice == "4":
        limit = int(input("How many pages to analyze? (default: 10): ") or "10")
        pages = get_pages_with_most_media(limit)
        results = []
        for page in pages:
            result = analyze_page_with_media(page.id, extended_context=True)
            if result:
                results.append({"page_analyses": [result]})

    elif choice == "5":
        keyword = input("Enter search keyword: ")
        limit = int(input("How many matching pages to analyze? (default: 10): ") or "10")
        session = get_db_session()
        try:
            pages = session.query(Page).filter(
                (Page.title.like(f"%{keyword}%")) |
                (Page.content_text.like(f"%{keyword}%"))
            ).order_by(desc(Page.crawled_at)).limit(limit).all()

            results = []
            for page in pages:
                result = analyze_page_with_media(page.id, extended_context=True)
                if result:
                    results.append({"page_analyses": [result]})
        finally:
            session.close()

    else:
        print("Invalid choice. Exiting.")
        exit(1)

    # Display and save results
    if results:
        print(f"\nAnalysis complete. Found {len(results)} results.")

        # Show summary
        for i, result in enumerate(results, 1):
            if "site" in result:  # Comprehensive site analysis
                print(f"\n--- Site Analysis {i} ---")
                print(f"Site: {result['site'].url}")
                print(f"Pages analyzed: {len(result['page_analyses'])}")
                print(f"Summary preview: {result['site_summary'][:200]}...")
            else:  # Individual page analysis
                for pa in result['page_analyses']:
                    print(f"\n--- Page Analysis ---")
                    print(f"URL: {pa['url']}")
                    print(f"Title: {pa['title']}")
                    print(f"Images: {pa['image_count']}")
                    print(f"Analysis preview: {pa['analysis'][:200]}...")

        # Ask if user wants to save results
        save = input("\nDo you want to save these results to a file? (y/n): ")
        if save.lower() == 'y':
            format_choice = input("Save format (txt/json) [default: txt]: ").lower() or "txt"
            filename = input("Enter filename (leave blank for automatic naming): ")
            save_comprehensive_analysis(results, filename if filename else None, format=format_choice)
    else:
        print("No results found.")