import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, ForeignKey, Index, LargeBinary, \
    Float, JSON, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.dialects.mysql import LONGTEXT, LONGBLOB

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
    forum_structures = relationship("ForumStructure", back_populates="site", cascade="all, delete-orphan")

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
    depth = Column(Integer, default=0)  # Add depth tracking for 3-level crawling

    # Relationship to site
    site = relationship("Site", back_populates="pages")
    media_files = relationship("MediaFile", back_populates="page", cascade="all, delete-orphan")

    # Create indexes for faster queries
    __table_args__ = (
        Index('idx_url', 'url'),
        Index('idx_site_id', 'site_id'),
        Index('idx_depth', 'depth'),
    )

    def __repr__(self):
        return f"<Page(url='{self.url}', title='{self.title}')>"


class MediaFile(Base):
    __tablename__ = 'media_files'

    id = Column(Integer, primary_key=True, autoincrement=True)
    page_id = Column(Integer, ForeignKey('pages.id'), nullable=False)
    url = Column(Text, nullable=False)
    file_type = Column(String(100), nullable=True)
    # Change from LargeBinary to LONGBLOB for MySQL to handle larger files
    content = Column(LONGBLOB, nullable=True)  # Can store up to 4GB
    description = Column(Text, nullable=True)
    size_bytes = Column(Integer, nullable=True)
    filename = Column(String(255), nullable=True)
    downloaded_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Add media categorization
    media_category = Column(String(50), nullable=True)  # 'image', 'audio', 'video', 'document', 'other'

    # MinIO storage references
    minio_bucket = Column(String(100), nullable=True)
    minio_object_name = Column(String(500), nullable=True)

    # Relationship
    page = relationship("Page", back_populates="media_files")

    # Add indexes for better performance
    __table_args__ = (
        Index('idx_media_page_id', 'page_id'),
        Index('idx_media_file_type', 'file_type'),
        Index('idx_media_category', 'media_category'),
        Index('idx_media_size', 'size_bytes'),
        Index('idx_media_downloaded', 'downloaded_at'),
        Index('idx_media_minio', 'minio_bucket', 'minio_object_name'),
    )

    def __repr__(self):
        return f"<MediaFile(id={self.id}, url='{self.url[:50]}...', type='{self.file_type}', size={self.size_bytes})>"


class ResearchTarget(Base):
    """Research target definition for AI analysis"""
    __tablename__ = "research_targets"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    keywords = Column(JSON, nullable=True)  # Store as JSON array
    target_domains = Column(JSON, nullable=True)  # Store domains as JSON array
    research_goals = Column(Text, nullable=True)
    priority = Column(Integer, default=1)  # 1-5 priority scale
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    content_analyses = relationship("ContentAnalysis", back_populates="research_target", cascade="all, delete-orphan")
    insights = relationship("DeepInsights", back_populates="research_target", cascade="all, delete-orphan")
    forum_structures = relationship("ForumStructure", back_populates="research_target", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<ResearchTarget(name='{self.name}', priority={self.priority})>"


class ForumStructure(Base):
    """Detected forum structures and organization"""
    __tablename__ = "forum_structure"

    id = Column(Integer, primary_key=True)
    research_target_id = Column(Integer, ForeignKey('research_targets.id'), nullable=True)
    site_id = Column(Integer, ForeignKey('sites.id'), nullable=False)
    structure_type = Column(String(50), nullable=False)  # 'board', 'thread', 'category', 'subforum'
    name = Column(String(255), nullable=True)
    url = Column(String(255), nullable=True)
    parent_structure_id = Column(Integer, ForeignKey('forum_structure.id'), nullable=True)
    description = Column(Text, nullable=True)
    member_count = Column(Integer, nullable=True)
    post_count = Column(Integer, nullable=True)
    activity_level = Column(String(20), nullable=True)  # 'high', 'medium', 'low'
    moderation_status = Column(String(20), nullable=True)  # 'active', 'minimal', 'none'
    language = Column(String(10), nullable=True)
    tags = Column(JSON, nullable=True)
    detected_at = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    research_target = relationship("ResearchTarget", back_populates="forum_structures")
    site = relationship("Site", back_populates="forum_structures")
    children = relationship("ForumStructure", remote_side=[id])

    __table_args__ = (
        Index('idx_research_target_structure', 'research_target_id'),
        Index('idx_site_structure', 'site_id'),
        Index('idx_structure_type', 'structure_type'),
    )

    def __repr__(self):
        return f"<ForumStructure(type='{self.structure_type}', name='{self.name}')>"


class ContentAnalysis(Base):
    """Stores AI analysis results for pages"""
    __tablename__ = "content_analysis"

    id = Column(Integer, primary_key=True)
    page_id = Column(Integer, ForeignKey('pages.id'), nullable=False)
    research_target_id = Column(Integer, ForeignKey('research_targets.id'), nullable=True)
    forum_structure_id = Column(Integer, ForeignKey('forum_structure.id'), nullable=True)
    analysis_type = Column(String(50), nullable=False)  # 'comprehensive', 'targeted', 'media', 'forum'
    summary = Column(Text, nullable=True)
    key_points = Column(JSON, nullable=True)  # Store as JSON array
    relevance_score = Column(Float, default=0.0)  # 0.0 to 1.0
    confidence_score = Column(Float, default=0.0)  # AI confidence in analysis
    full_analysis = Column(LONGTEXT, nullable=True)
    analysis_metadata = Column(JSON, nullable=True)
    analyzed_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    page = relationship("Page")
    research_target = relationship("ResearchTarget", back_populates="content_analyses")
    forum_structure = relationship("ForumStructure")

    __table_args__ = (
        Index('idx_page_analysis', 'page_id'),
        Index('idx_research_target', 'research_target_id'),
        Index('idx_relevance_score', 'relevance_score'),
        Index('idx_forum_structure', 'forum_structure_id'),
    )

    def __repr__(self):
        return f"<ContentAnalysis(page_id={self.page_id}, type='{self.analysis_type}', relevance={self.relevance_score})>"


class EntityExtraction(Base):
    """Named entity extraction results"""
    __tablename__ = "entity_extraction"

    id = Column(Integer, primary_key=True)
    page_id = Column(Integer, ForeignKey('pages.id'), nullable=False)
    entity_text = Column(String(255), nullable=False)
    entity_type = Column(String(50), nullable=False)  # PERSON, LOCATION, ORG, etc.
    confidence = Column(Float, default=0.0)
    context = Column(Text, nullable=True)  # Surrounding text context
    frequency = Column(Integer, default=1)  # How often this entity appears
    importance_score = Column(Float, default=0.0)
    entity_metadata = Column(JSON, nullable=True)
    extracted_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    page = relationship("Page")

    __table_args__ = (
        Index('idx_page_entity', 'page_id'),
        Index('idx_entity_type', 'entity_type'),
        Index('idx_entity_text', 'entity_text'),
    )

    def __repr__(self):
        return f"<EntityExtraction(text='{self.entity_text}', type='{self.entity_type}', confidence={self.confidence})>"


class SentimentAnalysis(Base):
    """Sentiment analysis results for pages"""
    __tablename__ = "sentiment_analysis"

    id = Column(Integer, primary_key=True)
    page_id = Column(Integer, ForeignKey('pages.id'), nullable=False)
    overall_sentiment = Column(String(20), nullable=False)  # positive, negative, neutral
    sentiment_score = Column(Float, default=0.0)  # -1.0 to 1.0
    confidence = Column(Float, default=0.0)
    emotional_indicators = Column(JSON, nullable=True)  # anger, joy, fear, etc.
    key_phrases = Column(JSON, nullable=True)
    sentiment_breakdown = Column(JSON, nullable=True)  # granular analysis
    analyzed_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    page = relationship("Page")

    __table_args__ = (
        Index('idx_page_sentiment', 'page_id'),
        Index('idx_overall_sentiment', 'overall_sentiment'),
    )

    def __repr__(self):
        return f"<SentimentAnalysis(page_id={self.page_id}, sentiment='{self.overall_sentiment}', score={self.sentiment_score})>"


class TopicClustering(Base):
    """Topic clustering and categorization results"""
    __tablename__ = "topic_clustering"

    id = Column(Integer, primary_key=True)
    page_id = Column(Integer, ForeignKey('pages.id'), nullable=False)
    primary_topic = Column(String(255), nullable=False)
    topic_probability = Column(Float, default=0.0)
    secondary_topics = Column(JSON, nullable=True)  # Additional topic assignments
    keywords = Column(JSON, nullable=True)  # Key terms for this topic
    topic_summary = Column(Text, nullable=True)
    cluster_id = Column(Integer, nullable=True)  # For grouping similar content
    analyzed_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    page = relationship("Page")

    __table_args__ = (
        Index('idx_page_topic', 'page_id'),
        Index('idx_primary_topic', 'primary_topic'),
        Index('idx_cluster_id', 'cluster_id'),
    )

    def __repr__(self):
        return f"<TopicClustering(page_id={self.page_id}, topic='{self.primary_topic}', probability={self.topic_probability})>"


class DeepInsights(Base):
    """Advanced AI-generated insights and intelligence"""
    __tablename__ = "deep_insights"

    id = Column(Integer, primary_key=True)
    research_target_id = Column(Integer, ForeignKey('research_targets.id'), nullable=False)
    insight_type = Column(String(50), nullable=False)  # 'pattern', 'trend', 'anomaly', 'risk'
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    confidence_level = Column(Float, default=0.0)  # 0.0 to 1.0
    supporting_evidence = Column(JSON, nullable=True)  # Links to pages/analyses
    risk_assessment = Column(JSON, nullable=True)  # Security/legal implications
    actionable_items = Column(JSON, nullable=True)  # Recommended actions
    additional_metadata = Column(JSON, nullable=True)
    generated_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    research_target = relationship("ResearchTarget", back_populates="insights")

    __table_args__ = (
        Index('idx_research_target_insight', 'research_target_id'),
        Index('idx_insight_type', 'insight_type'),
        Index('idx_confidence_level', 'confidence_level'),
    )

    def __repr__(self):
        return f"<DeepInsights(type='{self.insight_type}', title='{self.title}', confidence={self.confidence_level})>"


# Database schema migration function
def migrate_media_files_schema():
    """Add missing columns to existing media_files table"""
    try:
        with engine.connect() as connection:
            # Check if downloaded_at column exists
            result = connection.execute(text("""
                                             SELECT COLUMN_NAME
                                             FROM INFORMATION_SCHEMA.COLUMNS
                                             WHERE TABLE_SCHEMA = %s
                                               AND TABLE_NAME = 'media_files'
                                               AND COLUMN_NAME = 'downloaded_at'
                                             """), (DB_NAME,))

            if not result.fetchone():
                # Add downloaded_at column if it doesn't exist
                connection.execute(text("""
                                        ALTER TABLE media_files
                                            ADD COLUMN downloaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
                                        """))
                print("Added downloaded_at column to media_files table")

            connection.commit()
            return True
    except Exception as e:
        print(f"Error migrating media_files schema: {e}")
        return False


# Function to reset the database
def reset_database():
    try:
        # Use connection context manager
        with engine.connect() as connection:
            # Disable foreign key checks temporarily
            connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))

            # Drop all tables
            Base.metadata.drop_all(engine)

            # Re-enable foreign key checks
            connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
            connection.commit()

        # Create all tables
        Base.metadata.create_all(engine)
        return True
    except Exception as e:
        print(f"Error resetting database: {e}")
        # Make sure to re-enable foreign key checks even if there's an error
        try:
            with engine.connect() as connection:
                connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
                connection.commit()
        except:
            pass
        return False


# Alternative function that manually drops tables in the correct order
def reset_database_safe():
    try:
        # Drop tables in reverse dependency order to avoid foreign key issues
        tables_to_drop = [
            'deep_insights',
            'topic_clustering',
            'sentiment_analysis',
            'entity_extraction',
            'content_analysis',
            'forum_structure',
            'media_files',
            'pages',
            'research_targets',
            'sites'
        ]

        with engine.connect() as connection:
            for table in tables_to_drop:
                try:
                    connection.execute(text(f"DROP TABLE IF EXISTS {table}"))
                except Exception as e:
                    print(f"Warning: Could not drop table {table}: {e}")
            connection.commit()

        # Create all tables
        Base.metadata.create_all(engine)
        return True
    except Exception as e:
        print(f"Error resetting database: {e}")
        return False


# Function to add missing tables without dropping existing ones
def update_database_schema():
    """Add any missing tables or columns without dropping existing data"""
    try:
        # First run the migration for existing tables
        migrate_media_files_schema()

        # Then create any missing tables
        Base.metadata.create_all(engine)
        return True
    except Exception as e:
        print(f"Error updating database schema: {e}")
        return False


# Function to optimize database for unlimited media mode
def optimize_database_for_unlimited_media():
    """Apply additional optimizations for unlimited media file handling"""
    try:
        with engine.connect() as connection:
            # Set MySQL optimizations for large media handling
            optimizations = [
                "SET GLOBAL innodb_buffer_pool_size = 1073741824",  # 1GB buffer pool
                "SET GLOBAL innodb_log_file_size = 268435456",  # 256MB log file
                "SET GLOBAL innodb_flush_log_at_trx_commit = 2",  # Better performance for bulk inserts
                "SET GLOBAL innodb_file_per_table = 1",  # Separate file per table
                "SET GLOBAL query_cache_size = 67108864",  # 64MB query cache
                "SET GLOBAL tmp_table_size = 134217728",  # 128MB temp table size
                "SET GLOBAL max_heap_table_size = 134217728",  # 128MB heap table size
            ]

            for optimization in optimizations:
                try:
                    connection.execute(text(optimization))
                    print(f"Applied optimization: {optimization}")
                except Exception as e:
                    print(f"Could not apply optimization '{optimization}': {e}")

            connection.commit()
            return True
    except Exception as e:
        print(f"Error optimizing database: {e}")
        return False


# Function to create manual indexes if needed
def create_additional_indexes():
    """Create additional indexes manually for better performance"""
    try:
        with engine.connect() as connection:
            # Additional manual indexes for complex queries
            additional_indexes = [
                "CREATE INDEX IF NOT EXISTS idx_media_content_analysis ON media_files(page_id) WHERE content IS NOT NULL",
                "CREATE INDEX IF NOT EXISTS idx_large_media_files ON media_files(size_bytes) WHERE size_bytes > 1048576",
                # Files > 1MB
                "CREATE INDEX IF NOT EXISTS idx_recent_downloads ON media_files(downloaded_at) WHERE downloaded_at > DATE_SUB(NOW(), INTERVAL 7 DAY)",
                "CREATE INDEX IF NOT EXISTS idx_media_by_category_size ON media_files(media_category, size_bytes)",
            ]

            for index_sql in additional_indexes:
                try:
                    connection.execute(text(index_sql))
                    print(f"Created index: {index_sql}")
                except Exception as e:
                    print(f"Could not create index: {e}")

            connection.commit()
            return True
    except Exception as e:
        print(f"Error creating additional indexes: {e}")
        return False


# Create all tables
def init_db():
    try:
        # Run migration first
        migrate_media_files_schema()

        # Then create all tables
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