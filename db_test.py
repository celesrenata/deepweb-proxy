import os
import sys
import logging
from sqlalchemy import create_engine, text
import getpass

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Get MySQL connection details from environment or use defaults
DB_HOST = os.getenv("MYSQL_HOST", "10.1.1.12")
DB_PORT = os.getenv("MYSQL_PORT", "3306")
DB_USER = os.getenv("MYSQL_USER", "splinter-research")
DB_PASS = os.getenv("MYSQL_PASSWORD", "PSCh4ng3me!")
DB_NAME = os.getenv("MYSQL_DATABASE", "splinter-research")


def test_connection(create_db=False):
    """Test database connection and optionally create the database"""
    # Get password if not in environment
    global DB_PASS
    if not DB_PASS:
        DB_PASS = getpass.getpass(f"Enter MySQL password for {DB_USER}@{DB_HOST}: ")
        os.environ["MYSQL_PASSWORD"] = DB_PASS

    logger.info(f"Testing connection to MySQL at {DB_HOST}:{DB_PORT} as {DB_USER}")

    try:
        # First try to connect to the server without specifying a database
        root_url = f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/"
        engine = create_engine(root_url)

        with engine.connect() as conn:
            logger.info("Successfully connected to MySQL server")

            # Check if the database exists
            result = conn.execute(text(f"SHOW DATABASES LIKE '{DB_NAME}'"))
            db_exists = result.fetchone() is not None

            if not db_exists:
                if create_db:
                    logger.info(f"Creating database {DB_NAME}")
                    conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}`"))
                    logger.info(f"Database {DB_NAME} created successfully")
                else:
                    logger.error(f"Database {DB_NAME} does not exist")
                    return False
            else:
                logger.info(f"Database {DB_NAME} already exists")

        # Now try to connect to the specific database
        db_url = f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        db_engine = create_engine(db_url)

        with db_engine.connect() as conn:
            logger.info(f"Successfully connected to database {DB_NAME}")

            # Test a simple query
            version = conn.execute(text("SELECT VERSION()")).fetchone()[0]
            logger.info(f"MySQL version: {version}")

            # Test creating a simple table
            logger.info("Testing table creation...")
            conn.execute(text("""
                              CREATE TABLE IF NOT EXISTS connection_test
                              (
                                  id
                                  INT
                                  AUTO_INCREMENT
                                  PRIMARY
                                  KEY,
                                  test_date
                                  DATETIME
                                  DEFAULT
                                  CURRENT_TIMESTAMP
                              )
                              """))

            # Insert a record
            conn.execute(text("INSERT INTO connection_test (test_date) VALUES (NOW())"))

            # Check if it worked
            count = conn.execute(text("SELECT COUNT(*) FROM connection_test")).fetchone()[0]
            logger.info(f"Test table has {count} records")

            return True

    except Exception as e:
        logger.error(f"Database connection error: {e}")

        # Provide more specific advice based on the error
        error_str = str(e).lower()
        if "access denied" in error_str:
            logger.error("Authentication failed - check your username and password")
        elif "unknown database" in error_str:
            logger.error(f"Database '{DB_NAME}' doesn't exist. Run with --create-db to create it")
        elif "can't connect" in error_str or "connection refused" in error_str:
            logger.error(f"Could not connect to {DB_HOST}:{DB_PORT} - check if MySQL is running and accessible")

        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test MySQL database connection")
    parser.add_argument("--create-db", action="store_true", help="Create the database if it doesn't exist")
    parser.add_argument("--host", help="MySQL host")
    parser.add_argument("--port", help="MySQL port")
    parser.add_argument("--user", help="MySQL username")
    parser.add_argument("--database", help="MySQL database name")

    args = parser.parse_args()

    # Override environment variables with command line arguments
    if args.host:
        os.environ["MYSQL_HOST"] = args.host
    if args.port:
        os.environ["MYSQL_PORT"] = args.port
    if args.user:
        os.environ["MYSQL_USER"] = args.user
    if args.database:
        os.environ["MYSQL_DATABASE"] = args.database

    success = test_connection(create_db=args.create_db)

    if success:
        logger.info("Database connection test completed successfully")

        # Import and test the actual models
        try:
            import db_models

            logger.info("Successfully imported db_models")

            # Test initializing the database
            db_models.init_db()
            logger.info("Successfully initialized database schema")

            # Test creating a session
            session = db_models.get_db_session()
            logger.info("Successfully created database session")
            session.close()
        except Exception as e:
            logger.error(f"Error testing models: {e}")
    else:
        logger.error("Database connection test failed")

    sys.exit(0 if success else 1)