from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
import uvicorn
import os
import logging
from sqlalchemy import or_, and_, text
from typing import List, Optional
import getpass

# Import database models
from db_models import get_db_session, Site, Page, init_db

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI()

# Path to the sites list file
SITES_FILE = "/mnt/config/sites.txt"
# For development/testing, use a local file if the mount doesn't exist
if not os.path.exists(os.path.dirname(SITES_FILE)):
    SITES_FILE = "sites.txt"


class Config(BaseModel):
    sites: list[str]


class SiteResponse(BaseModel):
    id: int
    url: str
    is_onion: bool
    is_i2p: bool
    last_crawled: Optional[str] = None

    class Config:
        orm_mode = True


class PageResponse(BaseModel):
    id: int
    url: str
    title: Optional[str] = None
    snippet: str
    crawled_at: str

    class Config:
        orm_mode = True


class QueryRequest(BaseModel):
    query: str
    top_k: int = 5
    onion_only: bool = False
    i2p_only: bool = False


class SearchResult(BaseModel):
    site_url: str
    page_url: str
    title: str
    snippet: str
    crawled_at: str


def read_sites():
    """Read sites from the sites.txt file"""
    try:
        if os.path.exists(SITES_FILE):
            with open(SITES_FILE, "r") as f:
                return [line.strip() for line in f if line.strip()]
        else:
            logger.warning(f"Sites file {SITES_FILE} does not exist")
            return []
    except Exception as e:
        logger.error(f"Error reading sites file: {e}")
        return []


def write_sites(sites):
    """Write sites to the sites.txt file"""
    try:
        directory = os.path.dirname(SITES_FILE)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        with open(SITES_FILE, "w") as f:
            for site in sites:
                f.write(f"{site}\n")
        return True
    except Exception as e:
        logger.error(f"Error writing sites file: {e}")
        return False


@app.get("/config")
def get_config():
    sites = read_sites()
    return {"sites": sites}


@app.post("/config")
def update_config(config: Config):
    if write_sites(config.sites):
        # Update the sites in the database
        session = get_db_session()
        try:
            # Add new sites
            for site_url in config.sites:
                existing_site = session.query(Site).filter(Site.url == site_url).first()
                if not existing_site:
                    new_site = Site(
                        url=site_url,
                        is_onion=".onion" in site_url,
                        is_i2p=".i2p" in site_url
                    )
                    session.add(new_site)

            # Remove sites that are not in the config
            session.query(Site).filter(~Site.url.in_(config.sites)).delete(synchronize_session=False)

            session.commit()
            return {"sites": config.sites}
        except Exception as e:
            session.rollback()
            logger.error(f"Database error in update_config: {e}")
            raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
        finally:
            session.close()
    else:
        raise HTTPException(status_code=500, detail="Failed to update sites configuration")


@app.post("/add_site")
async def add_site(site_data: dict):
    site = site_data.get("url", "")
    if not site:
        raise HTTPException(status_code=400, detail="Missing 'url' field in request")

    # Add to sites.txt
    sites = read_sites()
    if site not in sites:
        sites.append(site)
        if not write_sites(sites):
            raise HTTPException(status_code=500, detail="Failed to update sites file")

    # Add to database
    session = get_db_session()
    try:
        existing_site = session.query(Site).filter(Site.url == site).first()
        if not existing_site:
            new_site = Site(
                url=site,
                is_onion=".onion" in site,
                is_i2p=".i2p" in site
            )
            session.add(new_site)
            session.commit()
            return {"status": "success", "message": f"Added {site}"}
        return {"status": "success", "message": f"Site {site} already exists"}
    except Exception as e:
        session.rollback()
        logger.error(f"Database error in add_site: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    finally:
        session.close()


@app.get("/sites", response_model=List[SiteResponse])
async def list_sites():
    """List all sites in the database"""
    session = get_db_session()
    try:
        sites = session.query(Site).all()
        result = []
        for site in sites:
            result.append({
                "id": site.id,
                "url": site.url,
                "is_onion": site.is_onion,
                "is_i2p": site.is_i2p,
                "last_crawled": site.last_crawled.isoformat() if site.last_crawled else None
            })
        return result
    except Exception as e:
        logger.error(f"Database error in list_sites: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    finally:
        session.close()


@app.get("/sites/{site_id}/pages", response_model=List[PageResponse])
async def list_site_pages(site_id: int):
    """List all pages for a specific site"""
    session = get_db_session()
    try:
        pages = session.query(Page).filter(Page.site_id == site_id).all()

        # Convert to response model format
        result = []
        for page in pages:
            snippet = page.content_text[:200] + "..." if page.content_text and len(
                page.content_text) > 200 else page.content_text
            result.append({
                "id": page.id,
                "url": page.url,
                "title": page.title,
                "snippet": snippet,
                "crawled_at": page.crawled_at.isoformat() if page.crawled_at else None
            })

        return result
    except Exception as e:
        logger.error(f"Database error in list_site_pages: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    finally:
        session.close()


@app.post("/query")
async def query_sites(request: QueryRequest):
    """Search for content in the database"""
    try:
        logger.info(f"Received query: {request.query}")
        session = get_db_session()

        try:
            # Build query filters
            filters = []

            # Add network type filters if specified
            if request.onion_only:
                filters.append(Site.is_onion == True)
            if request.i2p_only:
                filters.append(Site.is_i2p == True)

            # Add text search filter
            search_terms = [f"%{term}%" for term in request.query.split()]
            text_filters = []
            for term in search_terms:
                text_filters.append(Page.content_text.like(term))
                text_filters.append(Page.title.like(term))

            # Combine filters
            if filters:
                site_filter = and_(*filters)
            else:
                site_filter = True

            text_filter = or_(*text_filters)

            # Execute query
            query_results = session.query(Page, Site).join(Site) \
                .filter(site_filter) \
                .filter(text_filter) \
                .order_by(Page.crawled_at.desc()) \
                .limit(request.top_k) \
                .all()

            # Format results
            results = []
            for page, site in query_results:
                snippet = page.content_text[:200] + "..." if page.content_text and len(
                    page.content_text) > 200 else page.content_text
                results.append({
                    "site_url": site.url,
                    "page_url": page.url,
                    "title": page.title or "No title",
                    "snippet": snippet or "",
                    "crawled_at": page.crawled_at.isoformat() if page.crawled_at else None
                })

            return {"results": results, "query": request.query}

        except Exception as e:
            logger.error(f"Database error in query_sites: {e}")
            return {"results": [], "query": request.query, "error": f"Database error: {str(e)}"}
        finally:
            session.close()

    except Exception as e:
        logger.error(f"Error processing query: {str(e)}")
        return {"results": [], "query": request.query, "error": str(e)}


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Test database connection
        session = get_db_session()
        session.execute(text("SELECT 1"))
        session.close()
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {"status": "error", "database": "disconnected", "error": str(e)}


if __name__ == "__main__":
    logger.info("Starting webserver")

    # Request password if not set in environment
    if not os.getenv("MYSQL_PASSWORD"):
        password = getpass.getpass("Enter MySQL password for splinter-research: ")
        os.environ["MYSQL_PASSWORD"] = password

    # Initialize the database
    try:
        if not init_db():
            logger.error("Failed to initialize database, exiting")
            exit(1)
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        exit(1)

    # Create sites file if it doesn't exist
    if not os.path.exists(SITES_FILE):
        logger.info(f"Creating default sites file at {SITES_FILE}")
        directory = os.path.dirname(SITES_FILE)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        with open(SITES_FILE, "w") as f:
            f.write("https://news.ycombinator.com/\n")  # Default site

    uvicorn.run(app, host="0.0.0.0", port=8080)