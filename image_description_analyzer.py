import os
import base64
import requests
import logging
import time
import json
import gc
from io import BytesIO
from PIL import Image
from sqlalchemy import update, and_, func
from db_models import get_db_session, MediaFile
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('image_analysis.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Enhanced Configuration
OLLAMA_ENDPOINT = os.getenv("OLLAMA_ENDPOINT", "http://10.1.1.12:2701/api/generate")
MULTIMODAL_MODEL = os.getenv("MULTIMODAL_MODEL", "llava:latest")
MAX_IMAGE_SIZE = (1024, 1024)  # Increased for better quality
BATCH_SIZE = int(os.getenv("IMAGE_BATCH_SIZE", "10"))  # Smaller batches for stability
RATE_LIMIT_DELAY = float(os.getenv("RATE_LIMIT_DELAY", "2.0"))  # Increased delay
MAX_RETRIES = 3
RETRY_DELAY = 5
CONTEXT_WINDOW = 8192  # Larger context for detailed descriptions
REQUEST_TIMEOUT = 120  # Increased timeout for complex images

# Progress tracking
PROGRESS_CHECKPOINT_INTERVAL = 50  # Save progress every N images


class ImageAnalysisError(Exception):
    """Custom exception for image analysis errors"""
    pass


def get_progress_checkpoint():
    """Get the last processed image ID from checkpoint file"""
    checkpoint_file = "image_analysis_checkpoint.txt"
    try:
        if os.path.exists(checkpoint_file):
            with open(checkpoint_file, 'r') as f:
                return int(f.read().strip())
    except (ValueError, IOError):
        pass
    return 0


def save_progress_checkpoint(last_processed_id):
    """Save the last processed image ID to checkpoint file"""
    checkpoint_file = "image_analysis_checkpoint.txt"
    try:
        with open(checkpoint_file, 'w') as f:
            f.write(str(last_processed_id))
    except IOError as e:
        logger.warning(f"Failed to save checkpoint: {e}")


def clear_all_image_descriptions():
    """Delete all existing image descriptions to allow for complete reanalysis"""
    session = get_db_session()
    try:
        # Count images with descriptions before clearing
        described_count = session.query(MediaFile).filter(
            MediaFile.content != None,
            MediaFile.file_type.like('image/%'),
            MediaFile.description != None,
            MediaFile.description != ''
        ).count()

        if described_count == 0:
            logger.info("No image descriptions found to clear")
            return 0

        logger.info(f"Found {described_count} images with descriptions")

        # Confirm the operation
        confirmation = input(
            f"\nThis will delete descriptions for {described_count} images. Are you sure? (yes/no): ").strip().lower()

        if confirmation not in ['yes', 'y']:
            logger.info("Operation cancelled by user")
            return 0

        # Clear all image descriptions
        logger.info("Clearing all image descriptions...")

        updated_count = session.execute(
            update(MediaFile)
            .where(and_(
                MediaFile.content != None,
                MediaFile.file_type.like('image/%'),
                MediaFile.description != None,
                MediaFile.description != ''
            ))
            .values(description=None)
        ).rowcount

        session.commit()

        # Clear checkpoint file to start fresh
        checkpoint_file = "image_analysis_checkpoint.txt"
        if os.path.exists(checkpoint_file):
            os.remove(checkpoint_file)
            logger.info("Cleared progress checkpoint")

        logger.info(f"Successfully cleared descriptions for {updated_count} images")
        return updated_count

    except Exception as e:
        logger.error(f"Error clearing image descriptions: {e}")
        session.rollback()
        return 0
    finally:
        session.close()


def delete_specific_image_descriptions(filter_criteria=None):
    """Delete descriptions for specific images based on criteria"""
    session = get_db_session()
    try:
        base_query = session.query(MediaFile).filter(
            MediaFile.content != None,
            MediaFile.file_type.like('image/%'),
            MediaFile.description != None,
            MediaFile.description != ''
        )

        print("\nAvailable filter options:")
        print("1. All images")
        print("2. Images with error descriptions")
        print("3. Images from specific file types")
        print("4. Images smaller than specific size")
        print("5. Images larger than specific size")
        print("6. Images with descriptions shorter than X characters")
        print("7. Images with descriptions longer than X characters")

        choice = input("\nSelect filter option (1-7): ").strip()

        if choice == "1":
            # All images - already handled by clear_all_image_descriptions
            return clear_all_image_descriptions()

        elif choice == "2":
            # Images with error descriptions
            error_patterns = [
                "Error:", "Failed to", "Unable to", "Connection error",
                "Request timeout", "Service unavailable", "API error",
                "Image processing failed", "No meaningful description"
            ]

            filter_query = base_query
            for pattern in error_patterns:
                filter_query = filter_query.filter(MediaFile.description.like(f"%{pattern}%"))

        elif choice == "3":
            # Specific file types
            print("\nCommon image file types found:")
            file_types = session.query(MediaFile.file_type, func.count(MediaFile.id)).filter(
                MediaFile.file_type.like('image/%')
            ).group_by(MediaFile.file_type).all()

            for i, (file_type, count) in enumerate(file_types, 1):
                print(f"{i}. {file_type} ({count} files)")

            selected_types = input(
                "Enter file type(s) to clear (comma separated, e.g., 'image/jpeg,image/png'): ").strip()
            if not selected_types:
                logger.info("No file types specified")
                return 0

            type_list = [t.strip() for t in selected_types.split(',')]
            filter_query = base_query.filter(MediaFile.file_type.in_(type_list))

        elif choice == "4":
            # Images smaller than specific size
            max_size = input("Enter maximum size in bytes: ").strip()
            if not max_size.isdigit():
                logger.error("Invalid size specified")
                return 0

            filter_query = base_query.filter(MediaFile.size_bytes <= int(max_size))

        elif choice == "5":
            # Images larger than specific size
            min_size = input("Enter minimum size in bytes: ").strip()
            if not min_size.isdigit():
                logger.error("Invalid size specified")
                return 0

            filter_query = base_query.filter(MediaFile.size_bytes >= int(min_size))

        elif choice == "6":
            # Short descriptions
            max_length = input("Enter maximum description length: ").strip()
            if not max_length.isdigit():
                logger.error("Invalid length specified")
                return 0

            filter_query = base_query.filter(func.length(MediaFile.description) <= int(max_length))

        elif choice == "7":
            # Long descriptions
            min_length = input("Enter minimum description length: ").strip()
            if not min_length.isdigit():
                logger.error("Invalid length specified")
                return 0

            filter_query = base_query.filter(func.length(MediaFile.description) >= int(min_length))

        else:
            logger.error("Invalid choice")
            return 0

        # Count matching images
        matching_count = filter_query.count()

        if matching_count == 0:
            logger.info("No images match the specified criteria")
            return 0

        logger.info(f"Found {matching_count} images matching the criteria")
        confirmation = input(f"Delete descriptions for these {matching_count} images? (yes/no): ").strip().lower()

        if confirmation not in ['yes', 'y']:
            logger.info("Operation cancelled by user")
            return 0

        # Get the IDs to update
        image_ids = [img.id for img in filter_query.all()]

        # Clear descriptions
        updated_count = session.execute(
            update(MediaFile)
            .where(MediaFile.id.in_(image_ids))
            .values(description=None)
        ).rowcount

        session.commit()
        logger.info(f"Successfully cleared descriptions for {updated_count} images")
        return updated_count

    except Exception as e:
        logger.error(f"Error clearing specific image descriptions: {e}")
        session.rollback()
        return 0
    finally:
        session.close()


def get_all_unprocessed_images(batch_size=BATCH_SIZE, resume_from_checkpoint=True):
    """
    Retrieve all unprocessed images with enhanced filtering and checkpoint support.
    """
    session = get_db_session()
    try:
        # Base query for unprocessed images
        base_query = session.query(MediaFile).filter(
            MediaFile.content != None,
            MediaFile.content != b'',  # Exclude empty content
            MediaFile.size_bytes > 100,  # Exclude tiny files that are likely corrupted
            and_(
                MediaFile.file_type.like('image/%'),
                ~MediaFile.file_type.like('%svg%')  # Exclude SVG files
            ),
            (MediaFile.description == None) | (MediaFile.description == '')
        )

        # Resume from checkpoint if requested
        last_processed_id = 0
        if resume_from_checkpoint:
            last_processed_id = get_progress_checkpoint()
            if last_processed_id > 0:
                logger.info(f"Resuming from checkpoint: last processed ID {last_processed_id}")
                base_query = base_query.filter(MediaFile.id > last_processed_id)

        # Order by ID for consistent processing
        base_query = base_query.order_by(MediaFile.id)

        # Get total count
        total_count = base_query.count()
        logger.info(f"Found {total_count} unprocessed images in the database")

        if total_count == 0:
            yield [], 0, 0
            return

        # Process in batches
        offset = 0
        processed_count = 0

        while True:
            batch = base_query.limit(batch_size).offset(offset).all()

            if not batch:
                break

            logger.info(
                f"Retrieved batch of {len(batch)} images (offset: {offset}, processed: {processed_count}/{total_count})")
            yield batch, total_count, processed_count

            processed_count += len(batch)
            offset += batch_size

            # Force garbage collection after each batch
            gc.collect()

    except Exception as e:
        logger.error(f"Error retrieving images: {e}")
        yield [], 0, 0
    finally:
        session.close()


def validate_and_resize_image(image_data):
    """Validate and resize image data with enhanced error handling"""
    if not image_data or len(image_data) < 100:
        raise ImageAnalysisError("Image data is too small or empty")

    try:
        # Try to open and validate the image
        image = Image.open(BytesIO(image_data))

        # Check if image is valid
        image.verify()

        # Reopen for processing (verify() closes the image)
        image = Image.open(BytesIO(image_data))

        # Check minimum dimensions
        if image.size[0] < 50 or image.size[1] < 50:
            raise ImageAnalysisError("Image dimensions too small")

        # Resize if necessary
        if image.size[0] > MAX_IMAGE_SIZE[0] or image.size[1] > MAX_IMAGE_SIZE[1]:
            image.thumbnail(MAX_IMAGE_SIZE, Image.Resampling.LANCZOS)

        # Convert to RGB if necessary
        if image.mode not in ('RGB', 'L'):
            image = image.convert('RGB')

        # Save to buffer
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=90, optimize=True)
        return buffer.getvalue()

    except Exception as e:
        raise ImageAnalysisError(f"Image processing failed: {str(e)}")


def image_to_base64(image_data):
    """Convert image data to base64 string with validation"""
    try:
        # Validate and resize the image
        processed_image = validate_and_resize_image(image_data)

        # Encode to base64
        base64_str = base64.b64encode(processed_image).decode('utf-8')

        # Validate the base64 encoding
        try:
            base64.b64decode(base64_str)
        except Exception:
            raise ImageAnalysisError("Invalid base64 encoding")

        logger.debug(f"Base64 encoding successful, length: {len(base64_str)}")
        return base64_str

    except ImageAnalysisError:
        raise
    except Exception as e:
        raise ImageAnalysisError(f"Base64 conversion failed: {str(e)}")


def describe_image_with_ai(image_data, context_info=None):
    """
    Enhanced image description with better prompting and error handling.
    """
    if not image_data:
        raise ImageAnalysisError("No image data provided")

    # Get base64 encoding
    try:
        image_base64 = image_to_base64(image_data)
    except ImageAnalysisError as e:
        logger.warning(f"Image processing failed: {e}")
        return f"Image processing failed: {str(e)}"

    # Enhanced prompt for better descriptions
    context_prompt = ""
    if context_info:
        context_prompt = f"Context: This image was found on {context_info.get('site_url', 'unknown site')} on page '{context_info.get('page_title', 'unknown page')}'. "

    detailed_prompt = f"""{context_prompt}Please provide a comprehensive description of this image including:

1. **Main Subject**: What is the primary focus of the image?
2. **Visual Details**: Colors, composition, style, quality
3. **Text Content**: Any visible text, signs, or writing (transcribe exactly)
4. **Context Clues**: Setting, environment, time period indicators
5. **Technical Aspects**: Image quality, format, any artifacts
6. **Content Classification**: Type of content (photo, artwork, screenshot, diagram, etc.)
7. **Notable Features**: Anything unusual, significant, or identifying

Provide a detailed, factual description suitable for indexing and search purposes."""

    # Attempt API call with retries
    for attempt in range(MAX_RETRIES):
        try:
            logger.debug(f"Sending image description request (attempt {attempt + 1}/{MAX_RETRIES})")

            request_payload = {
                "model": MULTIMODAL_MODEL,
                "prompt": detailed_prompt,
                "images": [image_base64],
                "stream": False,
                "options": {
                    "num_ctx": CONTEXT_WINDOW,
                    "temperature": 0.3,  # Lower temperature for more consistent results
                    "top_p": 0.9,
                    "repeat_penalty": 1.1
                }
            }

            response = requests.post(
                OLLAMA_ENDPOINT,
                json=request_payload,
                timeout=REQUEST_TIMEOUT
            )

            if response.status_code == 200:
                response_json = response.json()
                description = response_json.get("response", "").strip()

                if description and len(description) > 20:  # Minimum meaningful description
                    logger.debug(f"Successfully generated description (length: {len(description)})")
                    return description
                else:
                    logger.warning("Empty or too short response from model")
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(RETRY_DELAY)
                        continue
                    return "No meaningful description generated"

            elif response.status_code == 503:  # Service unavailable
                logger.warning(f"Service unavailable (503), retrying in {RETRY_DELAY} seconds...")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY)
                    continue
                return "Service temporarily unavailable"

            else:
                logger.error(f"API error: {response.status_code} - {response.text}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY)
                    continue
                return f"API error: {response.status_code}"

        except requests.exceptions.Timeout:
            logger.warning(f"Request timeout on attempt {attempt + 1}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
                continue
            return "Request timeout - image too complex or service overloaded"

        except requests.exceptions.ConnectionError:
            logger.warning(f"Connection error on attempt {attempt + 1}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * 2)  # Longer wait for connection issues
                continue
            return "Connection error - service unreachable"

        except Exception as e:
            logger.error(f"Unexpected error on attempt {attempt + 1}: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
                continue
            return f"Unexpected error: {str(e)}"

    return "Failed to generate description after multiple attempts"


def get_image_context_info(media_file):
    """Get contextual information about where the image was found"""
    session = get_db_session()
    try:
        # Get page and site information
        from db_models import Page, Site

        page = session.query(Page).filter(Page.id == media_file.page_id).first()
        if page:
            site = session.query(Site).filter(Site.id == page.site_id).first()
            return {
                'site_url': site.url if site else 'unknown',
                'page_title': page.title or 'untitled',
                'page_url': page.url or 'unknown'
            }
    except Exception as e:
        logger.warning(f"Failed to get context info: {e}")
    finally:
        session.close()

    return {}


def update_image_description(media_id, description):
    """Update the description field with enhanced error handling"""
    session = get_db_session()
    try:
        # Ensure description is not too long for database
        if len(description) > 65535:  # TEXT field limit
            description = description[:65532] + "..."
            logger.warning(f"Description truncated for media_id {media_id}")

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
    """Process a batch of images with enhanced error handling and progress tracking"""
    results = []

    for i, media_file in enumerate(batch):
        try:
            logger.info(f"Processing image {i + 1}/{len(batch)} - ID: {media_file.id}, filename: {media_file.filename}")

            # Get context information
            context_info = get_image_context_info(media_file)

            # Generate description
            description = describe_image_with_ai(media_file.content, context_info)

            # Update database
            success = update_image_description(media_file.id, description)

            result = {
                "media_id": media_file.id,
                "filename": media_file.filename,
                "success": success,
                "description_length": len(description),
                "description_preview": description[:150] + "..." if len(description) > 150 else description
            }

            if not success:
                result["error"] = "Database update failed"

            results.append(result)

            # Save progress checkpoint periodically
            if media_file.id % PROGRESS_CHECKPOINT_INTERVAL == 0:
                save_progress_checkpoint(media_file.id)

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

    return results


def analyze_all_images(resume_from_checkpoint=True):
    """Main function with enhanced progress tracking and error recovery"""
    total_processed = 0
    total_successful = 0
    start_time = time.time()

    logger.info("Starting enhanced image analysis process")

    # Create detailed log file for this session
    session_log = f"image_analysis_session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    session_results = []

    try:
        for batch, total_count, processed_so_far in get_all_unprocessed_images(
                resume_from_checkpoint=resume_from_checkpoint):
            if not batch:
                break

            logger.info(f"Processing batch of {len(batch)} images...")
            batch_start_time = time.time()

            batch_results = process_image_batch(batch)
            session_results.extend(batch_results)

            # Count successes
            successful = sum(1 for r in batch_results if r.get("success", False))
            total_processed += len(batch)
            total_successful += successful

            batch_time = time.time() - batch_start_time
            completion_percentage = ((processed_so_far + len(batch)) / total_count) * 100 if total_count > 0 else 0

            # Enhanced progress logging
            logger.info(f"Batch completed in {batch_time:.1f}s")
            logger.info(
                f"Progress: {processed_so_far + len(batch)}/{total_count} images ({completion_percentage:.1f}%)")
            logger.info(f"Batch success rate: {successful}/{len(batch)} ({successful / len(batch) * 100:.1f}%)")
            logger.info(
                f"Overall success rate: {total_successful}/{total_processed} ({total_successful / total_processed * 100:.1f}%)")

            # Save progress to session log
            with open(session_log, 'a') as f:
                f.write(f"Batch completed at {datetime.now()}: {successful}/{len(batch)} successful\n")

            # Update checkpoint with last processed ID
            if batch:
                save_progress_checkpoint(batch[-1].id)

    except KeyboardInterrupt:
        logger.info("Process interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error in analysis process: {e}")

    elapsed_time = time.time() - start_time
    success_rate = (total_successful / total_processed * 100) if total_processed > 0 else 0

    # Final summary
    logger.info("=" * 60)
    logger.info("IMAGE ANALYSIS COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Total images processed: {total_processed}")
    logger.info(f"Successfully described: {total_successful}")
    logger.info(f"Success rate: {success_rate:.1f}%")
    logger.info(f"Total time: {elapsed_time:.1f} seconds")
    logger.info(
        f"Average time per image: {elapsed_time / total_processed:.2f} seconds" if total_processed > 0 else "N/A")
    logger.info(f"Session log saved to: {session_log}")

    # Save detailed results
    results_file = f"image_analysis_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    try:
        with open(results_file, 'w') as f:
            json.dump({
                'summary': {
                    'total_processed': total_processed,
                    'total_successful': total_successful,
                    'success_rate': success_rate,
                    'elapsed_time': elapsed_time,
                    'start_time': datetime.fromtimestamp(start_time).isoformat(),
                    'end_time': datetime.now().isoformat()
                },
                'results': session_results
            }, f, indent=2)
        logger.info(f"Detailed results saved to: {results_file}")
    except Exception as e:
        logger.error(f"Failed to save results file: {e}")

    return {
        "total_images": total_processed,
        "successful": total_successful,
        "elapsed_time": elapsed_time,
        "success_rate": success_rate
    }


def get_enhanced_image_stats():
    """Get comprehensive statistics about images and their descriptions"""
    session = get_db_session()
    try:
        # Basic counts
        total_media = session.query(MediaFile).count()

        total_images = session.query(MediaFile).filter(
            MediaFile.content != None,
            MediaFile.file_type.like('image/%')
        ).count()

        described_images = session.query(MediaFile).filter(
            MediaFile.content != None,
            MediaFile.file_type.like('image/%'),
            MediaFile.description != None,
            MediaFile.description != ''
        ).count()

        # Size statistics
        size_stats = session.query(
            func.avg(MediaFile.size_bytes).label('avg_size'),
            func.min(MediaFile.size_bytes).label('min_size'),
            func.max(MediaFile.size_bytes).label('max_size')
        ).filter(
            MediaFile.content != None,
            MediaFile.file_type.like('image/%')
        ).first()

        # File type distribution
        file_types = session.query(
            MediaFile.file_type,
            func.count(MediaFile.id).label('count')
        ).filter(
            MediaFile.content != None,
            MediaFile.file_type.like('image/%')
        ).group_by(MediaFile.file_type).all()

        # Description length statistics
        desc_stats = session.query(
            func.avg(func.length(MediaFile.description)).label('avg_desc_length'),
            func.min(func.length(MediaFile.description)).label('min_desc_length'),
            func.max(func.length(MediaFile.description)).label('max_desc_length')
        ).filter(
            MediaFile.content != None,
            MediaFile.file_type.like('image/%'),
            MediaFile.description != None,
            MediaFile.description != ''
        ).first()

        return {
            "total_media_files": total_media,
            "total_images": total_images,
            "described_images": described_images,
            "percentage_described": (described_images / total_images * 100) if total_images > 0 else 0,
            "size_stats": {
                "average_bytes": int(size_stats.avg_size) if size_stats.avg_size else 0,
                "min_bytes": size_stats.min_size or 0,
                "max_bytes": size_stats.max_size or 0
            },
            "description_stats": {
                "average_length": int(desc_stats.avg_desc_length) if desc_stats.avg_desc_length else 0,
                "min_length": desc_stats.min_desc_length or 0,
                "max_length": desc_stats.max_desc_length or 0
            },
            "file_type_distribution": {ft.file_type: ft.count for ft in file_types}
        }
    except Exception as e:
        logger.error(f"Error getting enhanced image stats: {e}")
        return {"error": str(e)}
    finally:
        session.close()


def cleanup_failed_descriptions():
    """Clean up and retry images with error descriptions"""
    session = get_db_session()
    try:
        # Find images with error descriptions
        error_patterns = [
            "Error:", "Failed to", "Unable to", "Connection error",
            "Request timeout", "Service unavailable", "API error"
        ]

        error_count = 0
        for pattern in error_patterns:
            count = session.query(MediaFile).filter(
                MediaFile.description.like(f"%{pattern}%")
            ).count()
            error_count += count

        if error_count > 0:
            logger.info(f"Found {error_count} images with error descriptions")

            # Clear error descriptions to allow reprocessing
            for pattern in error_patterns:
                session.execute(
                    update(MediaFile)
                    .where(MediaFile.description.like(f"%{pattern}%"))
                    .values(description=None)
                )

            session.commit()
            logger.info(f"Cleared {error_count} error descriptions for reprocessing")

        return error_count

    except Exception as e:
        logger.error(f"Error cleaning up failed descriptions: {e}")
        session.rollback()
        return 0
    finally:
        session.close()


def export_image_descriptions(output_format="json"):
    """Export all image descriptions to a file"""
    session = get_db_session()
    try:
        # Get all images with descriptions
        images = session.query(MediaFile).filter(
            MediaFile.content != None,
            MediaFile.file_type.like('image/%'),
            MediaFile.description != None,
            MediaFile.description != ''
        ).all()

        if not images:
            logger.info("No image descriptions found to export")
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if output_format.lower() == "json":
            filename = f"image_descriptions_export_{timestamp}.json"
            export_data = []

            for img in images:
                export_data.append({
                    'id': img.id,
                    'filename': img.filename,
                    'url': img.url,
                    'file_type': img.file_type,
                    'size_bytes': img.size_bytes,
                    'description': img.description,
                    'downloaded_at': img.downloaded_at.isoformat() if img.downloaded_at else None
                })

            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)

        else:  # CSV format
            filename = f"image_descriptions_export_{timestamp}.csv"
            import csv

            with open(filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['ID', 'Filename', 'URL', 'File Type', 'Size (bytes)', 'Description', 'Downloaded At'])

                for img in images:
                    writer.writerow([
                        img.id,
                        img.filename,
                        img.url,
                        img.file_type,
                        img.size_bytes,
                        img.description,
                        img.downloaded_at.isoformat() if img.downloaded_at else ''
                    ])

        logger.info(f"Exported {len(images)} image descriptions to {filename}")
        return filename

    except Exception as e:
        logger.error(f"Error exporting image descriptions: {e}")
        return None
    finally:
        session.close()


if __name__ == "__main__":
    print("Enhanced Image Description Analyzer")
    print("=" * 50)

    # Show current stats
    print("Current database statistics:")
    stats = get_enhanced_image_stats()

    if "error" not in stats:
        print(f"Total media files: {stats['total_media_files']:,}")
        print(f"Total images: {stats['total_images']:,}")
        print(f"Images with descriptions: {stats['described_images']:,}")
        print(f"Percentage described: {stats['percentage_described']:.1f}%")
        print(f"Average image size: {stats['size_stats']['average_bytes']:,} bytes")

        if stats['described_images'] > 0:
            print(f"Average description length: {stats['description_stats']['average_length']:,} characters")

        print("\nFile type distribution:")
        for file_type, count in stats['file_type_distribution'].items():
            print(f"  {file_type}: {count:,} files")
    else:
        print(f"Error getting stats: {stats['error']}")

    print("\nOptions:")
    print("1. Analyze all unprocessed images")
    print("2. Resume from checkpoint")
    print("3. Clean up and retry failed descriptions")
    print("4. Delete ALL image descriptions and reanalyze")
    print("5. Delete specific image descriptions")
    print("6. View detailed statistics")
    print("7. Export image descriptions")
    print("8. Exit")

    choice = input("\nSelect an option (1-8): ").strip()

    if choice == "1":
        # Clear checkpoint for fresh start
        checkpoint_file = "image_analysis_checkpoint.txt"
        if os.path.exists(checkpoint_file):
            os.remove(checkpoint_file)
            print("Cleared previous checkpoint - starting fresh analysis")

        batch_size = input(f"Batch size (default: {BATCH_SIZE}): ").strip()
        if batch_size.isdigit():
            BATCH_SIZE = int(batch_size)

        delay = input(f"Delay between API calls in seconds (default: {RATE_LIMIT_DELAY}): ").strip()
        if delay and delay.replace('.', '', 1).isdigit():
            RATE_LIMIT_DELAY = float(delay)

        print("\nStarting fresh image analysis...")
        results = analyze_all_images(resume_from_checkpoint=False)

    elif choice == "2":
        print("Resuming from checkpoint...")
        results = analyze_all_images(resume_from_checkpoint=True)

    elif choice == "3":
        print("Cleaning up failed descriptions...")
        cleaned = cleanup_failed_descriptions()
        print(f"Cleaned up {cleaned} error descriptions")
        if cleaned > 0:
            print("You can now run option 1 or 2 to reprocess these images")

    elif choice == "4":
        print("=" * 60)
        print("DELETE ALL IMAGE DESCRIPTIONS AND REANALYZE")
        print("=" * 60)
        print("This will:")
        print("- Delete ALL existing image descriptions")
        print("- Clear progress checkpoint")
        print("- Start fresh analysis of all images")
        print("\nWARNING: This action cannot be undone!")

        cleared_count = clear_all_image_descriptions()

        if cleared_count > 0:
            print(f"\nSuccessfully cleared {cleared_count} descriptions")

            proceed = input("Proceed with reanalysis? (yes/no): ").strip().lower()
            if proceed in ['yes', 'y']:
                batch_size = input(f"Batch size (default: {BATCH_SIZE}): ").strip()
                if batch_size.isdigit():
                    BATCH_SIZE = int(batch_size)

                delay = input(f"Delay between API calls in seconds (default: {RATE_LIMIT_DELAY}): ").strip()
                if delay and delay.replace('.', '', 1).isdigit():
                    RATE_LIMIT_DELAY = float(delay)

                print("\nStarting complete reanalysis...")
                results = analyze_all_images(resume_from_checkpoint=False)
            else:
                print("Reanalysis cancelled. Descriptions have been cleared.")

    elif choice == "5":
        print("Delete specific image descriptions based on criteria...")
        cleared_count = delete_specific_image_descriptions()
        print(f"Cleared {cleared_count} descriptions")

    elif choice == "6":
        print("\nDetailed Statistics:")
        stats = get_enhanced_image_stats()
        print(json.dumps(stats, indent=2))

    elif choice == "7":
        print("Export image descriptions...")
        format_choice = input("Export format (json/csv): ").strip().lower()
        if format_choice not in ['json', 'csv']:
            format_choice = 'json'

        filename = export_image_descriptions(format_choice)
        if filename:
            print(f"Export completed: {filename}")
        else:
            print("Export failed")

    elif choice == "8":
        print("Exiting...")
        exit(0)

    else:
        print("Invalid choice. Exiting.")
        exit(1)

    if choice in ["1", "2", "4"] and 'results' in locals():
        print("\n" + "=" * 50)
        print("ANALYSIS COMPLETE")
        print("=" * 50)
        print(f"Processed: {results['total_images']:,} images")
        print(f"Successful: {results['successful']:,} images")
        print(f"Success rate: {results['success_rate']:.1f}%")
        print(f"Time elapsed: {results['elapsed_time']:.1f} seconds")

        # Show updated stats
        print("\nUpdated database statistics:")
        updated_stats = get_enhanced_image_stats()
        if "error" not in updated_stats:
            print(f"Total images: {updated_stats['total_images']:,}")
            print(f"Images with descriptions: {updated_stats['described_images']:,}")
            print(f"Percentage described: {updated_stats['percentage_described']:.1f}%")