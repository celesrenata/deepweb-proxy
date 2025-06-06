import os
import base64
import requests
import logging
import json
from io import BytesIO
from PIL import Image
from datetime import datetime
from sqlalchemy import desc, func
from db_models import get_db_session, Page, Site, MediaFile

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
OLLAMA_ENDPOINT = os.getenv("OLLAMA_ENDPOINT", "http://10.1.1.12:2701/api/generate")
MULTIMODAL_MODEL = os.getenv("MULTIMODAL_MODEL", "llava:latest")  # Change to your multimodal model
TEXT_MODEL = os.getenv("TEXT_MODEL", "llama3.1:8b")  # For text-only analysis
MAX_IMAGES_PER_PAGE = 5  # Limit images to process per page
MAX_IMAGE_SIZE = (800, 800)  # Resize large images to save bandwidth


def get_recent_pages(limit=10):
    """Retrieve the most recently crawled pages from the database."""
    session = get_db_session()
    try:
        pages = session.query(Page).order_by(desc(Page.crawled_at)).limit(limit).all()
        return pages
    except Exception as e:
        logger.error(f"Error retrieving pages: {e}")
        return []
    finally:
        session.close()


def get_page_with_media(page_id):
    """Get a page with its associated media files."""
    session = get_db_session()
    try:
        page = session.query(Page).filter(Page.id == page_id).first()
        if not page:
            return None

        # Fetch associated media files
        media_files = session.query(MediaFile).filter(
            MediaFile.page_id == page_id,
            MediaFile.content != None  # Only get files with content
        ).limit(MAX_IMAGES_PER_PAGE).all()

        return page, media_files
    except Exception as e:
        logger.error(f"Error retrieving page with media: {e}")
        return None, []
    finally:
        session.close()


def get_pages_with_most_media(limit=10):
    """Get pages that have the most media files."""
    session = get_db_session()
    try:
        # Subquery to count media files per page
        media_count = session.query(
            MediaFile.page_id,
            func.count(MediaFile.id).label('media_count')
        ).group_by(MediaFile.page_id).subquery()

        # Join with pages and order by media count
        pages = session.query(Page).join(
            media_count,
            Page.id == media_count.c.page_id
        ).order_by(
            desc(media_count.c.media_count)
        ).limit(limit).all()

        return pages
    except Exception as e:
        logger.error(f"Error retrieving pages with most media: {e}")
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


def process_with_multimodal_ai(text, images=None, prompt=None):
    """Process text and images with a multimodal AI model."""
    if not text and not images:
        return "No content provided for analysis."

    if not prompt:
        prompt = "Analyze this content and provide a detailed analysis. If images are present, describe what you see in them and how they relate to the text."

    # Create a multimodal prompt with text and images
    messages = [{"role": "user", "content": prompt + "\n\n" + text}]

    # Add images to the message if available
    if images and len(images) > 0:
        for idx, img_base64 in enumerate(images):
            if img_base64:
                img_msg = {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_base64}"
                            }
                        }
                    ]
                }
                messages.append(img_msg)

    try:
        # For multimodal models like LLaVA in Ollama
        response = requests.post(
            OLLAMA_ENDPOINT,
            json={
                "model": MULTIMODAL_MODEL,
                "messages": messages,
                "stream": False
            }
        )

        if response.status_code == 200:
            return response.json().get("message", {}).get("content", "No response from model")
        else:
            logger.error(f"API error: {response.status_code} - {response.text}")
            return f"Error: Failed to get response from AI model. Status code: {response.status_code}"

    except Exception as e:
        logger.error(f"Error communicating with multimodal AI model: {e}")
        return f"Error: {str(e)}"


def process_with_text_ai(text, prompt=None):
    """Process text with a text-only AI model."""
    if not text:
        return "No text provided for analysis."

    if not prompt:
        prompt = "Analyze this content and provide a detailed analysis with the following structure:\n1. Summary (1-2 paragraphs)\n2. Key Topics Identified\n3. Interesting Insights\n4. Potential Concerns or Red Flags\n5. Questions for Further Investigation"

    full_prompt = f"{prompt}\n\n{text}"

    try:
        response = requests.post(
            OLLAMA_ENDPOINT,
            json={
                "model": TEXT_MODEL,
                "prompt": full_prompt,
                "stream": False
            }
        )

        if response.status_code == 200:
            return response.json().get("response", "No response from model")
        else:
            logger.error(f"API error: {response.status_code} - {response.text}")
            return f"Error: Failed to get response from AI model. Status code: {response.status_code}"

    except Exception as e:
        logger.error(f"Error communicating with AI model: {e}")
        return f"Error: {str(e)}"


def analyze_page_with_media(page_id):
    """Analyze a page and its media files using the multimodal AI model."""
    page, media_files = get_page_with_media(page_id)

    if not page:
        logger.warning(f"Page not found with ID: {page_id}")
        return None

    logger.info(f"Analyzing page with media: {page.url}")

    # Prepare text content
    content = f"Title: {page.title}\n\n{page.content_text}"

    # Prepare image content
    images = []
    image_descriptions = []

    for idx, media in enumerate(media_files):
        if is_image_file(media.file_type) and media.content:
            img_base64 = image_to_base64(media.content)
            if img_base64:
                images.append(img_base64)
                image_descriptions.append(f"Image {idx + 1}: {media.filename or 'Unnamed'} ({media.file_type})")

    # Add image descriptions to the content
    if image_descriptions:
        content += "\n\nAttached Images:\n" + "\n".join(image_descriptions)

    # Choose the appropriate processing method
    if images:
        logger.info(f"Processing page {page.id} with {len(images)} images using multimodal model")
        analysis = process_with_multimodal_ai(content, images)
    else:
        logger.info(f"Processing page {page.id} with text-only model")
        analysis = process_with_text_ai(content)

    return {
        "page_id": page.id,
        "url": page.url,
        "title": page.title,
        "crawled_at": page.crawled_at,
        "image_count": len(images),
        "analysis": analysis
    }


def analyze_recent_content_with_media(limit=10):
    """Analyze recent pages including their media content."""
    pages = get_recent_pages(limit)

    if not pages:
        logger.warning("No pages found in the database.")
        return []

    results = []
    for page in pages:
        result = analyze_page_with_media(page.id)
        if result:
            results.append(result)

    return results


def analyze_pages_with_most_images(limit=10):
    """Analyze pages that have the most images."""
    pages = get_pages_with_most_media(limit)

    if not pages:
        logger.warning("No pages with media found in the database.")
        return []

    results = []
    for page in pages:
        result = analyze_page_with_media(page.id)
        if result:
            results.append(result)

    return results


def search_pages_with_media(keyword, limit=10):
    """Search for pages containing a keyword and analyze them with their media."""
    session = get_db_session()
    try:
        # Search for pages containing the keyword
        pages = session.query(Page).filter(
            (Page.title.like(f"%{keyword}%")) |
            (Page.content_text.like(f"%{keyword}%"))
        ).order_by(desc(Page.crawled_at)).limit(limit).all()

        if not pages:
            logger.warning(f"No pages found containing keyword: {keyword}")
            return []

        results = []
        for page in pages:
            result = analyze_page_with_media(page.id)
            if result:
                results.append(result)

        return results

    except Exception as e:
        logger.error(f"Error searching for keyword {keyword}: {e}")
        return []
    finally:
        session.close()


def save_analysis_results(results, filename=None, format="txt"):
    """Save analysis results to a file in the specified format."""
    if not results:
        logger.warning("No results to save.")
        return None

    if not filename:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"analysis_results_{timestamp}.{format}"

    if format.lower() == "json":
        # Convert datetime objects to strings for JSON serialization
        serializable_results = []
        for result in results:
            serializable_result = result.copy()
            if isinstance(serializable_result.get('crawled_at'), datetime):
                serializable_result['crawled_at'] = serializable_result['crawled_at'].isoformat()
            serializable_results.append(serializable_result)

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(serializable_results, f, indent=2)
    else:
        # Default to text format
        with open(filename, "w", encoding="utf-8") as f:
            for result in results:
                f.write(f"URL: {result['url']}\n")
                f.write(f"Title: {result['title']}\n")
                f.write(f"Crawled at: {result['crawled_at']}\n")
                f.write(f"Image count: {result.get('image_count', 0)}\n")
                f.write(f"Analysis:\n{result['analysis']}\n")
                f.write("-" * 80 + "\n\n")

    logger.info(f"Analysis results saved to {filename}")
    return filename


if __name__ == "__main__":
    print("Enhanced AI Analysis with Image Processing")
    print("1. Analyze recent content with media")
    print("2. Analyze pages with the most images")
    print("3. Search and analyze content containing a keyword")

    choice = input("Choose an option (1-3): ")

    if choice == "1":
        limit = int(input("How many recent pages to analyze? (default: 10): ") or "10")
        results = analyze_recent_content_with_media(limit)

    elif choice == "2":
        limit = int(input("How many pages to analyze? (default: 10): ") or "10")
        results = analyze_pages_with_most_images(limit)

    elif choice == "3":
        keyword = input("Enter search keyword: ")
        limit = int(input("How many matching pages to analyze? (default: 10): ") or "10")
        results = search_pages_with_media(keyword, limit)

    else:
        print("Invalid choice. Exiting.")
        exit(1)

    # Print analysis results
    if results:
        for i, result in enumerate(results, 1):
            print(f"\n--- Result {i} ---")
            print(f"URL: {result['url']}")
            print(f"Title: {result['title']}")
            print(f"Image count: {result.get('image_count', 0)}")
            print(f"Analysis: {result['analysis'][:200]}...")  # Show preview

        # Ask if user wants to save results
        save = input("\nDo you want to save these results to a file? (y/n): ")
        if save.lower() == 'y':
            format_choice = input("Save format (txt/json) [default: txt]: ").lower() or "txt"
            filename = input("Enter filename (leave blank for automatic naming): ")
            save_analysis_results(results, filename if filename else None, format=format_choice)
    else:
        print("No results found.")