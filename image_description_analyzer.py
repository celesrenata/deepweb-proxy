import os
import base64
import requests
import logging
import time
from io import BytesIO
from PIL import Image
from sqlalchemy import update
from db_models import get_db_session, MediaFile

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration - use the same as in ai_analysis.py for consistency
OLLAMA_ENDPOINT = os.getenv("OLLAMA_ENDPOINT", "http://10.1.1.12:2701/api/generate")
MULTIMODAL_MODEL = os.getenv("MULTIMODAL_MODEL", "llava:latest")
MAX_IMAGE_SIZE = (800, 800)  # Resize large images to save bandwidth
BATCH_SIZE = 50  # Process images in batches to avoid memory issues
RATE_LIMIT_DELAY = 1  # Seconds to wait between API calls to avoid rate limiting


def get_all_unprocessed_images(batch_size=BATCH_SIZE):
    """
    Retrieve all media files from the database that:
    1. Are images
    2. Have content
    3. Don't yet have a description
    Returns them in batches to avoid memory issues.
    """
    session = get_db_session()
    try:
        # Get the total count first
        total_count = session.query(MediaFile).filter(
            MediaFile.content != None,
            (MediaFile.file_type.like('image/%') | MediaFile.file_type.like('%image%')),
            (MediaFile.description == None) | (MediaFile.description == '')
        ).count()

        logger.info(f"Found {total_count} unprocessed images in the database")

        # Process in batches
        offset = 0
        while True:
            batch = session.query(MediaFile).filter(
                MediaFile.content != None,
                (MediaFile.file_type.like('image/%') | MediaFile.file_type.like('%image%')),
                (MediaFile.description == None) | (MediaFile.description == '')
            ).limit(batch_size).offset(offset).all()

            if not batch:
                break

            logger.info(f"Retrieved batch of {len(batch)} images (offset: {offset})")
            yield batch, total_count

            offset += batch_size

    except Exception as e:
        logger.error(f"Error retrieving images: {e}")
        yield [], 0
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


def get_base64_encoded_image(image_data):
    """
    Convert binary image data to base64 encoded string.
    Make sure to strip any whitespace or newlines.
    """
    if not image_data:
        return None

    # Ensure we're working with bytes
    if not isinstance(image_data, bytes):
        logger.warning("Image data is not bytes type")
        try:
            image_data = bytes(image_data)
        except Exception as e:
            logger.error(f"Failed to convert image data to bytes: {e}")
            return None

    try:
        # Encode to base64 and convert to string
        base64_str = base64.b64encode(image_data).decode('utf-8')

        # Remove any whitespace, newlines, etc.
        base64_str = base64_str.strip()

        # Verify the base64 string is valid
        try:
            # This will raise an error if the string is not valid base64
            base64.b64decode(base64_str)
            logger.debug(f"Base64 encoding successful, length: {len(base64_str)}")
            return base64_str
        except Exception as e:
            logger.error(f"Invalid base64 string produced: {e}")
            return None
    except Exception as e:
        logger.error(f"Error encoding image to base64: {e}")
        return None


def describe_image_with_ai(image_data):
    """
    Use multimodal AI to generate a description of an image.
    Returns the description as a string.
    """
    if not image_data:
        return "Unable to process image data: No image data provided."

    try:
        # Convert image to a standard format with strict controls
        img = Image.open(BytesIO(image_data))

        # Convert to RGB mode (required for JPEG)
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGB')

        # Strictly limit image size
        max_size = 512  # Even smaller size to ensure compatibility
        if img.width > max_size or img.height > max_size:
            img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

        # Save as PNG (more reliable than JPEG for this case)
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        processed_image_data = buffer.getvalue()
        logger.info("Converted image to PNG format")

        # Use a direct URL-based approach instead of base64
        # This creates a temporary file for the image
        import tempfile
        import os

        temp_dir = tempfile.gettempdir()
        temp_file_path = os.path.join(temp_dir, f"temp_image_{hash(processed_image_data)}.png")

        with open(temp_file_path, "wb") as f:
            f.write(processed_image_data)

        logger.info(f"Saved image to temporary file: {temp_file_path}")

        # Use the file path directly in the Ollama API call
        # This avoids base64 encoding issues completely
        request_payload = {
            "model": MULTIMODAL_MODEL,
            "prompt": f"Describe this image in detail: {temp_file_path}",
            "stream": False
        }

        logger.info(f"Sending request to model {MULTIMODAL_MODEL} with image path")

        response = requests.post(
            OLLAMA_ENDPOINT,
            json=request_payload,
            timeout=30
        )

        # Clean up temporary file
        try:
            os.remove(temp_file_path)
        except:
            pass

        if response.status_code == 200:
            response_json = response.json()
            logger.info(f"Response status: {response.status_code}")
            description = response_json.get("response", "")

            if not description:
                logger.warning("Empty response from model.")
                return "No description generated (empty model response)"

            return description
        else:
            logger.error(f"API error: {response.status_code} - {response.text}")

            # Try an entirely different approach - using a data URI
            try:
                logger.info("Trying data URI approach...")

                # Create a new PNG buffer with strict settings
                buffer = BytesIO()
                img.save(buffer, format="PNG", compress_level=9)
                buffer.seek(0)

                # Encode image with very careful handling
                import binascii
                base64_str = binascii.b2a_base64(buffer.read(), newline=False).decode('ascii')

                # Create a data URI
                data_uri = f"data:image/png;base64,{base64_str}"

                # Use the chat API format
                chat_payload = {
                    "model": MULTIMODAL_MODEL,
                    "messages": [
                        {
                            "role": "user",
                            "content": "Please describe this image in detail.",
                            "images": [data_uri]
                        }
                    ]
                }

                chat_endpoint = OLLAMA_ENDPOINT.replace("/api/generate", "/api/chat")

                alt_response = requests.post(
                    chat_endpoint,
                    json=chat_payload,
                    timeout=30
                )

                if alt_response.status_code == 200:
                    alt_json = alt_response.json()
                    if "message" in alt_json and "content" in alt_json["message"]:
                        return alt_json["message"]["content"]

                # Last fallback - try with a very simple base64 approach
                fallback_payload = {
                    "model": MULTIMODAL_MODEL,
                    "prompt": "Describe this image in detail.",
                    "images": [base64_str.strip()]
                }

                fallback_response = requests.post(
                    OLLAMA_ENDPOINT,
                    json=fallback_payload,
                    timeout=30
                )

                if fallback_response.status_code == 200:
                    return fallback_response.json().get("response", "")

            except Exception as alt_err:
                logger.error(f"Alternative approach failed: {alt_err}")

            # If all approaches fail, provide basic image metadata
            return f"Image description unavailable. Image dimensions: {img.width}x{img.height}, Format: {img.format}"

    except Exception as e:
        logger.error(f"Error in image processing: {e}")
        return f"Error processing image: {str(e)}"

def fix_image_description_analyzer():
    # Open the file
    with open('image_description_analyzer.py', 'r') as file:
        content = file.read()

    # Replace the problematic section
    # The issue is at the end where there's test code that doesn't belong
    problematic_code = """# Then when calling describe_image_with_ai:
image_content = get_image_content(session, media_file)
if image_content:
    description = describe_image_with_ai(image_content)
else:
    description = "Unable to retrieve image content\""""

    # Replace with a proper implementation in the process_image_batch function
    fixed_code = """# This function is called in process_image_batch"""

    # Do the replacement
    new_content = content.replace(problematic_code, fixed_code)

    # Write the fixed content back to the file
    with open('image_description_analyzer.py', 'w') as file:
        file.write(new_content)

    return "Fixed the image_description_analyzer.py file by removing the test code."


def fix_process_image_batch():
    """Update the process_image_batch function to correctly use image content"""
    with open('image_description_analyzer.py', 'r') as file:
        content = file.read()

    # Find the process_image_batch function
    process_image_batch_code = """def process_image_batch(batch):
    \"\"\"Process a batch of images, generating descriptions and updating the database.\"\"\"
    results = []

    for media_file in batch:
        try:
            logger.info(f"Processing image ID: {media_file.id}, filename: {media_file.filename}")

            # Convert image to base64
            img_base64 = image_to_base64(media_file.content)
            if not img_base64:
                logger.warning(f"Failed to convert image {media_file.id} to base64")
                continue

            # Generate description
            description = describe_image_with_ai(img_base64)

            # Update database
            success = update_image_description(media_file.id, description)

            results.append({
                "media_id": media_file.id,
                "filename": media_file.filename,
                "success": success,
                "description": description[:100] + "..." if len(description) > 100 else description
            })

            # Rate limiting
            time.sleep(RATE_LIMIT_DELAY)

        except Exception as e:
            logger.error(f"Error processing image {media_file.id}: {e}")
            results.append({
                "media_id": media_file.id,
                "filename": media_file.filename,
                "success": False,
                "error": str(e)
            })

    return results"""

    # Updated function that properly uses the image data
    updated_function = """def process_image_batch(batch):
    \"\"\"Process a batch of images, generating descriptions and updating the database.\"\"\"
    results = []
    session = get_db_session()

    try:
        for media_file in batch:
            try:
                logger.info(f"Processing image ID: {media_file.id}, filename: {media_file.filename}")

                # Use media_file.content directly with describe_image_with_ai
                description = describe_image_with_ai(media_file.content)

                # Update database
                success = update_image_description(media_file.id, description)

                results.append({
                    "media_id": media_file.id,
                    "filename": media_file.filename,
                    "success": success,
                    "description": description[:100] + "..." if len(description) > 100 else description
                })

                # Rate limiting
                time.sleep(RATE_LIMIT_DELAY)

            except Exception as e:
                logger.error(f"Error processing image {media_file.id}: {e}")
                results.append({
                    "media_id": media_file.id,
                    "filename": media_file.filename,
                    "success": False,
                    "error": str(e)
                })
    finally:
        session.close()

    return results"""

    # Replace the function with the updated version
    new_content = content.replace(process_image_batch_code, updated_function)

    # Write the fixed content back to the file
    with open('image_description_analyzer.py', 'w') as file:
        file.write(new_content)

    return "Updated the process_image_batch function to correctly handle image data."


def update_image_description(media_id, description):
    """Update the description field in the MediaFile table for the given media_id."""
    session = get_db_session()
    try:
        session.execute(
            update(MediaFile)
            .where(MediaFile.id == media_id)
            .values(description=description)
        )
        session.commit()
        return True
    except Exception as e:
        logger.error(f"Error updating description for media_id {media_id}: {e}")
        session.rollback()
        return False
    finally:
        session.close()


def process_image_batch(batch):
    """Process a batch of images with multiple fallback methods."""
    results = []

    for media_file in batch:
        try:
            logger.info(f"Processing image ID: {media_file.id}, filename: {media_file.filename}")

            # Check file size and skip very large files
            if media_file.content and len(media_file.content) > 5 * 1024 * 1024:  # 5MB limit
                description = f"Large image file ({len(media_file.content) / 1024 / 1024:.1f} MB). Basic metadata: {media_file.filename} ({media_file.file_type if media_file.file_type else 'unknown type'})"
                success = update_image_description(media_file.id, description)
                results.append({
                    "media_id": media_file.id,
                    "filename": media_file.filename,
                    "success": success,
                    "description": description
                })
                continue

            # Try to get description through the enhanced function
            description = describe_image_with_ai(media_file.content)

            # Simple fallback for errors
            if description.startswith("Error") or description.startswith("Unable"):
                # Create a basic description with file metadata
                description = f"Image file: {media_file.filename or 'Unnamed'}"
                if media_file.file_type:
                    description += f" ({media_file.file_type})"
                description += f" - Size: {len(media_file.content) / 1024:.1f} KB"

            # Update database with whatever description we have
            success = update_image_description(media_file.id, description)

            results.append({
                "media_id": media_file.id,
                "filename": media_file.filename,
                "success": success,
                "description": description[:100] + "..." if len(description) > 100 else description
            })

            # Rate limiting
            time.sleep(RATE_LIMIT_DELAY)

        except Exception as e:
            logger.error(f"Error processing image {media_file.id}: {e}")
            # Create a fallback description even on error
            fallback = f"Image processing error - {media_file.filename or 'Unknown file'}"
            update_image_description(media_file.id, fallback)
            results.append({
                "media_id": media_file.id,
                "filename": media_file.filename,
                "success": False,
                "error": str(e),
                "description": fallback
            })

    return results

def get_image_content(session, media_file):
    """Retrieve the image content from the database."""
    try:
        if media_file.content:
            return media_file.content
        else:
            # If content is not stored in the database, try to load from disk
            image_path = os.path.join(MEDIA_STORAGE_PATH, media_file.filename)
            if os.path.exists(image_path):
                with open(image_path, 'rb') as f:
                    return f.read()
            else:
                logger.error(f"Image file not found: {image_path}")
                return None
    except Exception as e:
        logger.error(f"Error retrieving image content: {e}")
        return None

def analyze_all_images():
    """Main function to analyze all images in the database and add descriptions."""
    total_processed = 0
    total_successful = 0

    logger.info("Starting image analysis process")

    start_time = time.time()

    for batch, total_count in get_all_unprocessed_images():
        if not batch:
            break

        batch_results = process_image_batch(batch)

        # Count successes
        successful = sum(1 for r in batch_results if r.get("success", False))
        total_processed += len(batch)
        total_successful += successful

        # Log progress
        completion_percentage = (total_processed / total_count) * 100 if total_count > 0 else 0
        logger.info(f"Progress: {total_processed}/{total_count} images processed ({completion_percentage:.1f}%)")
        logger.info(f"Batch success rate: {successful}/{len(batch)} ({successful / len(batch) * 100:.1f}%)")

    elapsed_time = time.time() - start_time

    success_rate = (total_successful / total_processed * 100) if total_processed > 0 else 0
    logger.info(f"Image analysis complete. Processed {total_processed} images in {elapsed_time:.1f} seconds")
    logger.info(f"Success rate: {total_successful}/{total_processed} ({success_rate:.1f}%)")

    return {
        "total_images": total_processed,
        "successful": total_successful,
        "elapsed_time": elapsed_time
    }


def get_image_stats():
    """Get statistics about images in the database and their descriptions."""
    session = get_db_session()
    try:
        total_images = session.query(MediaFile).filter(
            MediaFile.content != None,
            (MediaFile.file_type.like('image/%') | MediaFile.file_type.like('%image%'))
        ).count()

        described_images = session.query(MediaFile).filter(
            MediaFile.content != None,
            (MediaFile.file_type.like('image/%') | MediaFile.file_type.like('%image%')),
            MediaFile.description != None,
            MediaFile.description != ''
        ).count()

        return {
            "total_images": total_images,
            "described_images": described_images,
            "percentage_described": (described_images / total_images * 100) if total_images > 0 else 0
        }
    except Exception as e:
        logger.error(f"Error getting image stats: {e}")
        return {
            "error": str(e)
        }
    finally:
        session.close()


if __name__ == "__main__":
    print("Image Description Analyzer")
    print("This tool analyzes all images in the database and adds AI-generated descriptions.")
    print("\nCurrent stats:")

    stats = get_image_stats()
    print(f"Total images in database: {stats.get('total_images', 'Unknown')}")
    print(f"Images with descriptions: {stats.get('described_images', 'Unknown')}")
    print(f"Percentage described: {stats.get('percentage_described', 0):.1f}%")

    proceed = input("\nDo you want to analyze all unprocessed images? (y/n): ")

    if proceed.lower() == 'y':
        batch_size = input(f"Batch size (default: {BATCH_SIZE}): ")
        if batch_size and batch_size.isdigit():
            BATCH_SIZE = int(batch_size)

        delay = input(f"Delay between API calls in seconds (default: {RATE_LIMIT_DELAY}): ")
        if delay and delay.replace('.', '', 1).isdigit():
            RATE_LIMIT_DELAY = float(delay)

        print("\nStarting image analysis process...")
        results = analyze_all_images()

        print("\nAnalysis complete!")
        print(f"Processed {results.get('total_images')} images in {results.get('elapsed_time'):.1f} seconds")
        print(f"Successfully described: {results.get('successful')} images")

        # Show updated stats
        print("\nUpdated stats:")
        stats = get_image_stats()
        print(f"Total images in database: {stats.get('total_images', 'Unknown')}")
        print(f"Images with descriptions: {stats.get('described_images', 'Unknown')}")
        print(f"Percentage described: {stats.get('percentage_described', 0):.1f}%")
    else:
        print("Operation cancelled.")


    def describe_image_with_ai_chat_api(image_data):
        """
        Alternative approach using Ollama's chat API endpoint for image description.
        """
        if not image_data:
            return "Unable to process image data: No image data provided."

        try:
            # Convert image to JPEG format
            try:
                img = Image.open(BytesIO(image_data))
                if img.mode in ('RGBA', 'LA', 'P'):
                    img = img.convert('RGB')
                buffer = BytesIO()
                img.save(buffer, format="JPEG", quality=95)
                processed_image_data = buffer.getvalue()
            except Exception as e:
                logger.warning(f"Failed to convert image format: {e}")
                processed_image_data = image_data

            # Encode to base64
            base64_str = base64.b64encode(processed_image_data).decode('utf-8')

            # For Ollama chat API
            chat_endpoint = OLLAMA_ENDPOINT.replace("/api/generate", "/api/chat")

            # Prepare the messages for the chat API
            request_payload = {
                "model": MULTIMODAL_MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": "Please describe this image in detail.",
                        "images": [f"data:image/jpeg;base64,{base64_str}"]
                    }
                ],
                "stream": False
            }

            logger.info(f"Sending request to chat API with model {MULTIMODAL_MODEL}")

            response = requests.post(
                chat_endpoint,
                json=request_payload,
                timeout=30
            )

            if response.status_code == 200:
                response_json = response.json()

                # Extract the response from the messages
                if "message" in response_json:
                    return response_json["message"]["content"]
                else:
                    return "No description generated (empty chat response)"
            else:
                logger.error(f"Chat API error: {response.status_code} - {response.text}")
                return f"Unable to process image with chat API: {response.status_code}"

        except Exception as e:
            logger.error(f"Error with chat API: {e}")
            return f"Error with chat API: {str(e)}"