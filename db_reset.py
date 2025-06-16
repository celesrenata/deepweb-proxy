#!/usr/bin/env python3
"""
Nuclear Database Reset Script
Completely destroys and recreates all database tables from scratch.
Now includes MinIO bucket cleanup functionality.
"""

import os
import sys
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.exc import SQLAlchemyError
import logging
from minio import Minio
from minio.error import S3Error

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Database configuration from environment
DB_HOST = os.getenv("MYSQL_HOST", "10.1.1.12")
DB_PORT = os.getenv("MYSQL_PORT", "3306")
DB_USER = os.getenv("MYSQL_USER", "splinter-research")
DB_PASS = os.getenv("MYSQL_PASSWORD", "PSCh4ng3me!")
DB_NAME = os.getenv("MYSQL_DATABASE", "splinter-research")

DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# MinIO configuration from environment
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"
MINIO_BUCKET_IMAGES = os.getenv("MINIO_BUCKET_IMAGES", "crawler-images")
MINIO_BUCKET_AUDIO = os.getenv("MINIO_BUCKET_AUDIO", "crawler-audio")
MINIO_BUCKET_VIDEO = os.getenv("MINIO_BUCKET_VIDEO", "crawler-videos")
MINIO_BUCKET_OTHER = os.getenv("MINIO_BUCKET_OTHER", "crawler-media")

# List of all crawler buckets
CRAWLER_BUCKETS = [
    MINIO_BUCKET_IMAGES,
    MINIO_BUCKET_AUDIO,
    MINIO_BUCKET_VIDEO,
    MINIO_BUCKET_OTHER
]


def get_minio_client():
    """Create and return MinIO client with better error handling"""
    if not MINIO_ACCESS_KEY or not MINIO_SECRET_KEY:
        print("❌ MinIO credentials not configured")
        return None

    try:
        # Clean the endpoint - remove any protocol prefix
        endpoint = MINIO_ENDPOINT.replace('http://', '').replace('https://', '')

        print(f"Connecting to MinIO at: {endpoint} (secure={MINIO_SECURE})")

        client = Minio(
            endpoint,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE
        )

        # Test connection with a simple operation
        try:
            buckets = client.list_buckets()
            print(f"✅ Connected to MinIO successfully. Found {len(buckets)} buckets.")
            return client
        except Exception as test_error:
            print(f"❌ MinIO connection test failed: {test_error}")
            return None

    except Exception as e:
        print(f"❌ Failed to create MinIO client: {e}")
        return None


def show_minio_info():
    """Show current MinIO bucket information"""
    print(f"\n📦 MinIO Bucket Information:")
    print(f"Endpoint: {MINIO_ENDPOINT}")
    print(f"Secure: {MINIO_SECURE}")

    if not MINIO_ACCESS_KEY or not MINIO_SECRET_KEY:
        print("❌ MinIO credentials not configured")
        return False

    client = get_minio_client()
    if not client:
        print("❌ Could not connect to MinIO")
        return False

    try:
        print(f"\n📊 Crawler Bucket Status:")
        total_objects = 0
        total_size = 0

        for bucket_name in CRAWLER_BUCKETS:
            try:
                if client.bucket_exists(bucket_name):
                    # Count objects in bucket
                    objects = list(client.list_objects(bucket_name, recursive=True))
                    object_count = len(objects)
                    bucket_size = sum(obj.size for obj in objects if obj.size)

                    total_objects += object_count
                    total_size += bucket_size

                    print(f"   {bucket_name}: {object_count:,} objects ({bucket_size / (1024 * 1024):.1f} MB)")
                else:
                    print(f"   {bucket_name}: ❌ Does not exist")

            except Exception as e:
                print(f"   {bucket_name}: ❌ Error checking bucket ({e})")

        print(f"\n📈 Total: {total_objects:,} objects ({total_size / (1024 * 1024):.1f} MB)")
        return True

    except Exception as e:
        print(f"❌ Error getting MinIO info: {e}")
        return False


def delete_all_minio_objects():
    """Delete all objects from crawler buckets"""
    print("🗑️  MINIO BUCKET CLEANUP 🗑️")
    print("This will DELETE ALL MEDIA FILES from the crawler buckets!")
    print(f"Buckets to be cleaned: {', '.join(CRAWLER_BUCKETS)}")

    confirmation = input("Type 'DELETE' to confirm: ").strip()
    if confirmation != "DELETE":
        print("❌ MinIO cleanup cancelled")
        return False

    client = get_minio_client()
    if not client:
        print("❌ Could not connect to MinIO")
        return False

    try:
        total_deleted = 0

        for bucket_name in CRAWLER_BUCKETS:
            try:
                if not client.bucket_exists(bucket_name):
                    print(f"   {bucket_name}: Bucket does not exist, skipping")
                    continue

                print(f"   Cleaning bucket: {bucket_name}")

                # List all objects in the bucket with error handling
                try:
                    objects = list(client.list_objects(bucket_name, recursive=True))
                except Exception as list_error:
                    print(f"   {bucket_name}: ❌ Error listing objects: {list_error}")
                    continue

                if not objects:
                    print(f"   {bucket_name}: Already empty")
                    continue

                print(f"   {bucket_name}: Found {len(objects)} objects to delete")

                # Delete objects one by one for better error handling
                deleted_count = 0
                failed_count = 0

                for i, obj in enumerate(objects):
                    try:
                        client.remove_object(bucket_name, obj.object_name)
                        deleted_count += 1

                        # Progress indicator
                        if (i + 1) % 100 == 0 or (i + 1) == len(objects):
                            print(f"     Progress: {i + 1}/{len(objects)} objects processed")

                    except Exception as delete_error:
                        failed_count += 1
                        logger.warning(f"Failed to delete {obj.object_name}: {delete_error}")

                        # If we get too many consecutive failures, stop
                        if failed_count > 10 and deleted_count == 0:
                            print(f"   {bucket_name}: ❌ Too many deletion failures, stopping")
                            break

                print(f"   {bucket_name}: ✅ Deleted {deleted_count} objects, {failed_count} failures")
                total_deleted += deleted_count

            except Exception as e:
                print(f"   {bucket_name}: ❌ Error cleaning bucket: {e}")
                logger.error(f"Bucket cleanup error for {bucket_name}: {e}")

        print(f"\n✅ MinIO cleanup completed! Deleted {total_deleted:,} total objects")
        return total_deleted > 0

    except Exception as e:
        print(f"❌ Error during MinIO cleanup: {e}")
        logger.error(f"General MinIO cleanup error: {e}")
        return False


def nuclear_delete_minio_buckets():
    """Nuclear option: Delete entire buckets and recreate them"""
    print("💥 NUCLEAR MINIO BUCKET DELETION 💥")
    print("This will DELETE ENTIRE BUCKETS and recreate them empty!")
    print(f"Buckets to be nuked: {', '.join(CRAWLER_BUCKETS)}")
    print("⚠️  This is more aggressive than just deleting objects!")

    confirmation = input("Type 'NUKE BUCKETS' to confirm: ").strip()
    if confirmation != "NUKE BUCKETS":
        print("❌ Nuclear bucket deletion cancelled")
        return False

    client = get_minio_client()
    if not client:
        print("❌ Could not connect to MinIO")
        return False

    try:
        for bucket_name in CRAWLER_BUCKETS:
            try:
                print(f"   Processing bucket: {bucket_name}")

                if client.bucket_exists(bucket_name):
                    print(f"     Deleting bucket: {bucket_name}")

                    # First try to remove all objects (required before bucket deletion)
                    try:
                        objects = client.list_objects(bucket_name, recursive=True)
                        object_names = [obj.object_name for obj in objects]
                        if object_names:
                            # Force delete all objects
                            delete_errors = list(client.remove_objects(bucket_name, object_names))
                            if delete_errors:
                                print(f"     Warning: Some objects couldn't be deleted")
                    except Exception as clear_error:
                        print(f"     Warning: Error clearing bucket: {clear_error}")

                    # Now delete the bucket
                    client.remove_bucket(bucket_name)
                    print(f"     ✅ Bucket {bucket_name} deleted")
                else:
                    print(f"     Bucket {bucket_name} doesn't exist")

                # Recreate the bucket
                print(f"     Creating new empty bucket: {bucket_name}")
                client.make_bucket(bucket_name)
                print(f"     ✅ Bucket {bucket_name} recreated")

            except Exception as e:
                print(f"   ❌ Error with bucket {bucket_name}: {e}")

        print(f"\n✅ Nuclear bucket deletion completed!")
        return True

    except Exception as e:
        print(f"❌ Error during nuclear bucket deletion: {e}")
        return False

def get_all_table_names(engine):
    """Get all table names from the database"""
    try:
        inspector = inspect(engine)
        return inspector.get_table_names()
    except Exception as e:
        logger.warning(f"Could not get table names: {e}")
        return []


def nuclear_reset_database():
    """
    Nuclear option: Completely destroy all tables and recreate from scratch
    """
    print("🚨 NUCLEAR DATABASE RESET 🚨")
    print("This will COMPLETELY DESTROY ALL DATA in the database!")
    print("Are you absolutely sure you want to continue?")

    confirmation = input("Type 'NUKE' to confirm: ").strip()
    if confirmation != "NUKE":
        print("❌ Operation cancelled")
        return False

    try:
        # Create engine
        engine = create_engine(DATABASE_URL, echo=False)

        print("\n🔍 Discovering existing tables...")

        # Get all existing table names
        existing_tables = get_all_table_names(engine)
        print(f"Found {len(existing_tables)} existing tables: {existing_tables}")

        with engine.connect() as connection:
            # Start transaction
            trans = connection.begin()

            try:
                print("\n💥 Disabling foreign key checks...")
                connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
                connection.execute(text("SET SESSION sql_mode = ''"))

                # Drop all existing tables one by one
                if existing_tables:
                    print(f"\n🗑️  Dropping {len(existing_tables)} tables...")
                    for table in existing_tables:
                        try:
                            print(f"   Dropping table: {table}")
                            connection.execute(text(f"DROP TABLE IF EXISTS `{table}`"))
                        except Exception as e:
                            print(f"   ⚠️  Warning dropping {table}: {e}")

                # Also try to drop any views that might exist
                print("\n🗑️  Dropping any remaining views...")
                try:
                    result = connection.execute(text("""
                                                     SELECT table_name
                                                     FROM information_schema.views
                                                     WHERE table_schema = DATABASE()
                                                     """))
                    views = [row[0] for row in result]
                    for view in views:
                        try:
                            print(f"   Dropping view: {view}")
                            connection.execute(text(f"DROP VIEW IF EXISTS `{view}`"))
                        except Exception as e:
                            print(f"   ⚠️  Warning dropping view {view}: {e}")
                except Exception as e:
                    print(f"   ⚠️  Could not check for views: {e}")

                # Re-enable foreign key checks
                print("\n🔧 Re-enabling foreign key checks...")
                connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

                # Commit the destruction
                trans.commit()
                print("✅ All tables successfully destroyed!")

            except Exception as e:
                print(f"❌ Error during table destruction: {e}")
                trans.rollback()
                raise

        # Now recreate all tables from models
        print("\n🏗️  Recreating tables from models...")
        try:
            # Import the models to get the metadata
            from db_models import Base

            # Create all tables
            Base.metadata.create_all(engine)
            print("✅ All tables successfully recreated!")

            # Verify tables were created
            new_tables = get_all_table_names(engine)
            print(f"✅ Verified {len(new_tables)} tables created: {new_tables}")

        except ImportError as e:
            print(f"❌ Could not import db_models: {e}")
            print("Make sure db_models.py is in the same directory or Python path")
            return False
        except Exception as e:
            print(f"❌ Error recreating tables: {e}")
            return False

        print("\n🎉 NUCLEAR RESET COMPLETED SUCCESSFULLY! 🎉")
        return True

    except SQLAlchemyError as e:
        print(f"❌ Database error: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False


def nuclear_reset_everything():
    """Nuclear reset of both database and MinIO buckets"""
    print("💥 NUCLEAR RESET EVERYTHING 💥")
    print("This will COMPLETELY DESTROY:")
    print("- ALL DATABASE TABLES AND DATA")
    print("- ALL MEDIA FILES IN MINIO BUCKETS")
    print("\nThis operation is IRREVERSIBLE!")

    confirmation = input("Type 'NUKE EVERYTHING' to confirm: ").strip()
    if confirmation != "NUKE EVERYTHING":
        print("❌ Operation cancelled")
        return False

    print("\n🚀 Starting complete nuclear reset...")

    # Reset database first
    print("\n" + "=" * 50)
    print("STEP 1: NUCLEAR DATABASE RESET")
    print("=" * 50)
    db_success = nuclear_reset_database()

    # Reset MinIO buckets
    print("\n" + "=" * 50)
    print("STEP 2: MINIO BUCKET CLEANUP")
    print("=" * 50)
    minio_success = delete_all_minio_objects()

    print("\n" + "=" * 50)
    print("NUCLEAR RESET SUMMARY")
    print("=" * 50)
    print(f"Database reset: {'✅ SUCCESS' if db_success else '❌ FAILED'}")
    print(f"MinIO cleanup: {'✅ SUCCESS' if minio_success else '❌ FAILED'}")

    if db_success and minio_success:
        print("\n🎉 COMPLETE NUCLEAR RESET SUCCESSFUL! 🎉")
        return True
    else:
        print("\n⚠️  Some operations failed. Check logs above.")
        return False


def verify_database_structure():
    """Verify the database structure after reset"""
    try:
        engine = create_engine(DATABASE_URL, echo=False)
        tables = get_all_table_names(engine)

        expected_tables = [
            'sites', 'pages', 'media_files', 'research_targets',
            'forum_structure', 'content_analysis', 'entity_extraction',
            'sentiment_analysis', 'topic_clustering', 'deep_insights'
        ]

        print(f"\n📊 Database Structure Verification:")
        print(f"Expected tables: {len(expected_tables)}")
        print(f"Actual tables: {len(tables)}")

        missing_tables = set(expected_tables) - set(tables)
        extra_tables = set(tables) - set(expected_tables)

        if missing_tables:
            print(f"❌ Missing tables: {missing_tables}")

        if extra_tables:
            print(f"⚠️  Extra tables: {extra_tables}")

        if not missing_tables and not extra_tables:
            print("✅ Database structure is correct!")
            return True
        else:
            return False

    except Exception as e:
        print(f"❌ Error verifying database: {e}")
        return False


def show_database_info():
    """Show current database information"""
    try:
        engine = create_engine(DATABASE_URL, echo=False)

        with engine.connect() as connection:
            # Get database info
            result = connection.execute(text("SELECT DATABASE() as db_name"))
            db_name = result.fetchone()[0]

            # Get table count
            tables = get_all_table_names(engine)

            print(f"\n📋 Current Database Information:")
            print(f"Database: {db_name}")
            print(f"Host: {DB_HOST}:{DB_PORT}")
            print(f"User: {DB_USER}")
            print(f"Tables: {len(tables)}")
            if tables:
                print(f"Table names: {', '.join(tables)}")

            # Get approximate row counts
            if tables:
                print(f"\n📊 Approximate row counts:")
                for table in tables:
                    try:
                        result = connection.execute(text(f"SELECT COUNT(*) FROM `{table}`"))
                        count = result.fetchone()[0]
                        print(f"   {table}: {count:,} rows")
                    except Exception as e:
                        print(f"   {table}: Error getting count ({e})")

    except Exception as e:
        print(f"❌ Error getting database info: {e}")


def main():
    """Main function with menu options"""
    print("=" * 60)
    print("🚨 NUCLEAR DATABASE & MINIO RESET UTILITY 🚨")
    print("=" * 60)

    while True:
        print("\nOptions:")
        print("1. Show current database information")
        print("2. Show MinIO bucket information")
        print("3. NUCLEAR DATABASE RESET (destroy and recreate all tables)")
        print("4. DELETE ALL MINIO OBJECTS (clean all crawler buckets)")
        print("5. 💥 NUCLEAR DELETE MINIO BUCKETS (delete and recreate buckets)")
        print("6. 💥 NUCLEAR RESET EVERYTHING (database + MinIO)")
        print("7. Verify database structure")
        print("8. Exit")

        choice = input("\nEnter your choice (1-8): ").strip()

        if choice == "1":
            show_database_info()

        elif choice == "2":
            show_minio_info()

        elif choice == "3":
            success = nuclear_reset_database()
            if success:
                verify_database_structure()

        elif choice == "4":
            delete_all_minio_objects()

        elif choice == "5":
            nuclear_delete_minio_buckets()

        elif choice == "6":
            nuclear_reset_everything()

        elif choice == "7":
            verify_database_structure()

        elif choice == "8":
            print("👋 Goodbye!")
            break

        else:
            print("❌ Invalid choice! Please enter 1-8.")


if __name__ == "__main__":
    main()