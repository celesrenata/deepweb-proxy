import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, ForeignKey, Index, LargeBinary
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

# Get database connection details from environment variables
DB_HOST = os.getenv("MYSQL_HOST", "10.1.1.12")
DB_PORT = os.getenv("MYSQL_PORT", "3306")
DB_USER = os.getenv("MYSQL_USER", "splinter-research")
DB_PASS = os.getenv("MYSQL_PASSWORD", "PSCh4ng3me!")
DB_NAME = os.getenv("MYSQL_DATABASE", "splinter-research")

# Create SQLAlchemy engine
DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
engine = create_engine(DATABASE_URL)

# Create declarative base
Base = declarative_base()


class Site(Base):
    __tablename__ = "sites"

    id = Column(Integer, primary_key=True)
    url = Column(String(255), unique=True, nullable=False)
    is_onion = Column(Boolean, default=False)
    is_i2p = Column(Boolean, default=False)
    last_crawled = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationship to pages
    pages = relationship("Page", back_populates="site", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Site(url='{self.url}')>"


class Page(Base):
    __tablename__ = "pages"

    id = Column(Integer, primary_key=True)
    site_id = Column(Integer, ForeignKey('sites.id'), nullable=False)
    url = Column(String(255), nullable=False)
    title = Column(Text, nullable=True)  # Changed from String(255) to Text
    content_text = Column(Text(16777215), nullable=True)  # Also using MEDIUMTEXT for content_text
    html_content = Column(Text(16777215), nullable=True)  # Using MEDIUMTEXT which can store up to 16MB
    crawled_at = Column(DateTime, default=datetime.utcnow)

    # Relationship to site
    site = relationship("Site", back_populates="pages")
    media_files = relationship("MediaFile", back_populates="page", cascade="all, delete-orphan")

    # Create indexes for faster queries
    __table_args__ = (
        Index('idx_url', 'url'),
        Index('idx_site_id', 'site_id'),
    )

    def __repr__(self):
        return f"<Page(url='{self.url}', title='{self.title}')>"

class MediaFile(Base):
    __tablename__ = "media_files"

    id = Column(Integer, primary_key=True)
    page_id = Column(Integer, ForeignKey('pages.id'), nullable=False)
    url = Column(String(255), nullable=False)
    file_type = Column(String(50), nullable=True)
    description = Column(Text, nullable=True)
    content = Column(LargeBinary(16777215), nullable=True)  # Using MEDIUMBLOB for binary data
    size_bytes = Column(Integer, nullable=True)
    filename = Column(String(255), nullable=True)
    downloaded_at = Column(DateTime, default=datetime.utcnow)

    # Relationship to page
    page = relationship("Page", back_populates="media_files")

    # Create indexes for faster queries
    __table_args__ = (
        Index('idx_page_id', 'page_id'),
        Index('idx_url', 'url'),
    )

    def __repr__(self):
        return f"<MediaFile(url='{self.url}', type='{self.file_type}', size={self.size_bytes})>"

# Function to reset the database
def reset_database():
    try:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        return True
    except Exception as e:
        print(f"Error resetting database: {e}")
        return False

# Create all tables
def init_db():
    try:
        Base.metadata.create_all(engine)
        return True
    except Exception as e:
        print(f"Error initializing database: {e}")
        return False


# Create session factory
Session = sessionmaker(bind=engine)


# Function to get a new session
def get_db_session():
    return Session()