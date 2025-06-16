#!/usr/bin/env python3
"""
AI-Powered Web Crawler for Deep Research
Based on mcp_engine.py with enhanced database schema for comprehensive research analysis
"""

import os
import json
import time
import logging
import requests
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from urllib.parse import urljoin, urlparse
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, ForeignKey, Index, LargeBinary, \
    Float, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.dialects.mysql import MEDIUMTEXT, LONGTEXT

# Import existing models
from db_models import Base, Site, Page, MediaFile, get_db_session, engine

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
OLLAMA_ENDPOINT = os.getenv("OLLAMA_ENDPOINT", "http://10.1.1.12:2701/api/generate")
RESEARCH_MODEL = os.getenv("RESEARCH_MODEL", "llama3.1:8b")
ENTITY_MODEL = os.getenv("ENTITY_MODEL", "llama3.1:8b")
SENTIMENT_MODEL = os.getenv("SENTIMENT_MODEL", "llama3.1:8b")

# Research-specific configuration
MAX_RESEARCH_DEPTH = int(os.getenv("MAX_RESEARCH_DEPTH", "5"))
ANALYSIS_BATCH_SIZE = int(os.getenv("ANALYSIS_BATCH_SIZE", "10"))
RESEARCH_FREQUENCY_HOURS = int(os.getenv("RESEARCH_FREQUENCY_HOURS", "24"))


class ResearchTarget(Base):
    """Stores research objectives and target keywords"""
    __tablename__ = "research_targets"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    keywords = Column(JSON, nullable=True)  # Store keywords as JSON array
    target_domains = Column(JSON, nullable=True)  # Specific domains to focus on
    research_goals = Column(Text, nullable=True)
    priority = Column(Integer, default=1)  # 1=highest, 5=lowest
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    content_analyses = relationship("ContentAnalysis", back_populates="research_target")
    insights = relationship("DeepInsights", back_populates="research_target")

    def __repr__(self):
        return f"<ResearchTarget(name='{self.name}', priority={self.priority})>"


class ContentAnalysis(Base):
    """Stores AI analysis results for pages"""
    __tablename__ = "content_analysis"

    id = Column(Integer, primary_key=True)
    page_id = Column(Integer, ForeignKey('pages.id'), nullable=False)
    research_target_id = Column(Integer, ForeignKey('research_targets.id'), nullable=True)
    analysis_type = Column(String(50), nullable=False)  # 'comprehensive', 'targeted', 'media'
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

    __table_args__ = (
        Index('idx_page_analysis', 'page_id'),
        Index('idx_research_target', 'research_target_id'),
        Index('idx_relevance_score', 'relevance_score'),
    )

    def __repr__(self):
        return f"<ContentAnalysis(page_id={self.page_id}, type='{self.analysis_type}', relevance={self.relevance_score})>"


class EntityExtraction(Base):
    """Stores extracted entities from content"""
    __tablename__ = "entity_extraction"

    id = Column(Integer, primary_key=True)
    page_id = Column(Integer, ForeignKey('pages.id'), nullable=False)
    entity_text = Column(String(500), nullable=False)
    entity_type = Column(String(50), nullable=False)  # PERSON, ORG, LOCATION, MISC
    confidence = Column(Float, default=0.0)
    context = Column(Text, nullable=True)  # Surrounding text context
    frequency = Column(Integer, default=1)  # How many times mentioned
    importance_score = Column(Float, default=0.0)
    entity_metadata = Column(JSON, nullable=True)  # Renamed from 'metadata'
    extracted_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    page = relationship("Page")

    __table_args__ = (
        Index('idx_entity_page', 'page_id'),
        Index('idx_entity_type', 'entity_type'),
        Index('idx_entity_text', 'entity_text'),
        Index('idx_importance_score', 'importance_score'),
    )

    def __repr__(self):
        return f"<EntityExtraction(text='{self.entity_text}', type='{self.entity_type}', importance={self.importance_score})>"


class SentimentAnalysis(Base):
    """Stores sentiment analysis results"""
    __tablename__ = "sentiment_analysis"

    id = Column(Integer, primary_key=True)
    page_id = Column(Integer, ForeignKey('pages.id'), nullable=False)
    overall_sentiment = Column(String(20), nullable=False)  # positive, negative, neutral
    sentiment_score = Column(Float, nullable=False)  # -1.0 to 1.0
    confidence = Column(Float, default=0.0)
    emotional_indicators = Column(JSON, nullable=True)  # anger, fear, joy, etc.
    key_phrases = Column(JSON, nullable=True)
    sentiment_breakdown = Column(JSON, nullable=True)  # paragraph-level sentiment
    analyzed_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    page = relationship("Page")

    __table_args__ = (
        Index('idx_sentiment_page', 'page_id'),
        Index('idx_sentiment_score', 'sentiment_score'),
        Index('idx_overall_sentiment', 'overall_sentiment'),
    )

    def __repr__(self):
        return f"<SentimentAnalysis(page_id={self.page_id}, sentiment='{self.overall_sentiment}', score={self.sentiment_score})>"


class TopicClustering(Base):
    """Stores topic modeling and clustering results"""
    __tablename__ = "topic_clustering"

    id = Column(Integer, primary_key=True)
    page_id = Column(Integer, ForeignKey('pages.id'), nullable=False)
    primary_topic = Column(String(100), nullable=False)
    topic_probability = Column(Float, default=0.0)
    secondary_topics = Column(JSON, nullable=True)  # [{topic, probability}, ...]
    keywords = Column(JSON, nullable=True)
    topic_summary = Column(Text, nullable=True)
    cluster_id = Column(String(50), nullable=True)  # For grouping similar content
    analyzed_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    page = relationship("Page")

    __table_args__ = (
        Index('idx_topic_page', 'page_id'),
        Index('idx_primary_topic', 'primary_topic'),
        Index('idx_cluster_id', 'cluster_id'),
    )

    def __repr__(self):
        return f"<TopicClustering(page_id={self.page_id}, topic='{self.primary_topic}', prob={self.topic_probability})>"



class DeepInsights(Base):
    """Stores advanced AI insights and correlations"""
    __tablename__ = "deep_insights"

    id = Column(Integer, primary_key=True)
    research_target_id = Column(Integer, ForeignKey('research_targets.id'), nullable=False)
    insight_type = Column(String(50), nullable=False)  # 'correlation', 'trend', 'anomaly', 'prediction'
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    confidence_level = Column(Float, default=0.0)
    supporting_evidence = Column(JSON, nullable=True)  # page_ids, entities, etc.
    risk_assessment = Column(String(20), nullable=True)  # low, medium, high, critical
    actionable_items = Column(JSON, nullable=True)
    additional_metadata = Column(JSON, nullable=True)  # Renamed from 'metadata'
    generated_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    research_target = relationship("ResearchTarget", back_populates="insights")

    __table_args__ = (
        Index('idx_insight_research', 'research_target_id'),
        Index('idx_insight_type', 'insight_type'),
        Index('idx_confidence_level', 'confidence_level'),
        Index('idx_risk_assessment', 'risk_assessment'),
    )

    def __repr__(self):
        return f"<DeepInsights(title='{self.title}', type='{self.insight_type}', confidence={self.confidence_level})>"


class AIResearchCrawler:
    """Enhanced AI-powered web crawler for deep research"""

    def __init__(self):
        self.session = get_db_session()
        self.setup_enhanced_database()

    def setup_enhanced_database(self):
        """Create enhanced database tables for research"""
        try:
            Base.metadata.create_all(engine)
            logger.info("Enhanced database schema created successfully")
            return True
        except Exception as e:
            logger.error(f"Error setting up enhanced database: {e}")
            return False

    def create_research_target(self, name: str, description: str, keywords: List[str],
                               target_domains: List[str] = None, research_goals: str = None,
                               priority: int = 1) -> Optional[ResearchTarget]:
        """Create a new research target"""
        try:
            research_target = ResearchTarget(
                name=name,
                description=description,
                keywords=keywords,
                target_domains=target_domains or [],
                research_goals=research_goals,
                priority=priority
            )

            self.session.add(research_target)
            self.session.commit()

            logger.info(f"Created research target: {name}")
            return research_target

        except Exception as e:
            logger.error(f"Error creating research target: {e}")
            self.session.rollback()
            return None

    def analyze_with_ollama(self, prompt: str, model: str = None, context: str = None) -> Optional[str]:
        """Generic function to communicate with Ollama for AI analysis"""
        if not model:
            model = RESEARCH_MODEL

        full_prompt = f"{prompt}\n\n{context}" if context else prompt

        try:
            response = requests.post(
                OLLAMA_ENDPOINT,
                json={
                    "model": model,
                    "prompt": full_prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,  # Lower temperature for more focused analysis
                        "top_p": 0.9
                    }
                },
                timeout=120
            )

            if response.status_code == 200:
                result = response.json().get("response", "")
                return result.strip()
            else:
                logger.error(f"Ollama API error: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logger.error(f"Error communicating with Ollama: {e}")
            return None

    def ai_content_analysis(self, page_id: int, research_target_id: int = None) -> Optional[ContentAnalysis]:
        """Perform comprehensive AI analysis of page content"""
        try:
            page = self.session.query(Page).filter(Page.id == page_id).first()
            if not page:
                logger.warning(f"Page not found: {page_id}")
                return None

            # Prepare content for analysis
            content = f"Title: {page.title}\n\nContent: {page.content_text}"

            # Get research context if target specified
            research_context = ""
            if research_target_id:
                target = self.session.query(ResearchTarget).filter(ResearchTarget.id == research_target_id).first()
                if target:
                    research_context = f"Research Focus: {target.name}\nKeywords: {', '.join(target.keywords or [])}\nGoals: {target.research_goals}"

            # Create comprehensive analysis prompt
            analysis_prompt = """
            Analyze the following content and provide a comprehensive research analysis with the following structure:

            1. EXECUTIVE SUMMARY (2-3 sentences)
            2. KEY FINDINGS (bullet points)
            3. RELEVANCE ASSESSMENT (rate 0-10 and explain)
            4. NOTABLE ENTITIES (people, organizations, locations)
            5. POTENTIAL CONCERNS OR RED FLAGS
            6. RESEARCH VALUE (why this content matters)
            7. RECOMMENDED FOLLOW-UP ACTIONS

            Be thorough but concise. Focus on actionable insights.
            """

            full_context = f"{research_context}\n\n{content}" if research_context else content
            analysis_result = self.analyze_with_ollama(analysis_prompt, context=full_context)

            if not analysis_result:
                logger.error(f"Failed to get AI analysis for page {page_id}")
                return None

            # Extract relevance score from analysis
            relevance_score = self._extract_relevance_score(analysis_result)

            # Create analysis record
            analysis = ContentAnalysis(
                page_id=page_id,
                research_target_id=research_target_id,
                analysis_type='comprehensive',
                summary=analysis_result[:1000],  # First 1000 chars as summary
                relevance_score=relevance_score,
                confidence_score=0.8,  # Default confidence
                full_analysis=analysis_result,
                analysis_metadata={
                    'model_used': RESEARCH_MODEL,
                    'analysis_version': '1.0',
                    'content_length': len(content)
                }
            )

            self.session.add(analysis)
            self.session.commit()

            logger.info(f"Created content analysis for page {page_id}")
            return analysis

        except Exception as e:
            logger.error(f"Error in AI content analysis: {e}")
            self.session.rollback()
            return None

    def extract_entities_ai(self, page_id: int) -> List[EntityExtraction]:
        """Extract named entities using AI"""
        try:
            page = self.session.query(Page).filter(Page.id == page_id).first()
            if not page:
                return []

            content = f"{page.title}\n\n{page.content_text}"

            entity_prompt = """
            Extract named entities from the following text. For each entity, provide:
            1. The entity text
            2. Entity type (PERSON, ORGANIZATION, LOCATION, MISC)
            3. Confidence level (0.0-1.0)
            4. Context (surrounding words)
            5. Importance (0.0-1.0 based on how significant this entity seems)

            Format as JSON array:
            [{"text": "entity", "type": "PERSON", "confidence": 0.9, "context": "surrounding text", "importance": 0.8}]

            Only extract entities that seem significant or relevant to research.
            """

            entities_result = self.analyze_with_ollama(entity_prompt, model=ENTITY_MODEL, context=content)

            if not entities_result:
                return []

            # Parse JSON response
            try:
                entities_data = json.loads(entities_result)
                if not isinstance(entities_data, list):
                    return []
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse entities JSON for page {page_id}")
                return []

            entities = []
            for entity_data in entities_data:
                if all(key in entity_data for key in ['text', 'type', 'confidence']):
                    entity = EntityExtraction(
                        page_id=page_id,
                        entity_text=entity_data['text'][:500],  # Limit length
                        entity_type=entity_data['type'],
                        confidence=float(entity_data.get('confidence', 0.0)),
                        context=entity_data.get('context', '')[:1000],
                        importance_score=float(entity_data.get('importance', 0.0)),
                        metadata=entity_data
                    )
                    entities.append(entity)

            # Bulk insert entities
            if entities:
                self.session.add_all(entities)
                self.session.commit()
                logger.info(f"Extracted {len(entities)} entities for page {page_id}")

            return entities

        except Exception as e:
            logger.error(f"Error extracting entities: {e}")
            self.session.rollback()
            return []

    def perform_sentiment_analysis(self, page_id: int) -> Optional[SentimentAnalysis]:
        """Perform AI-powered sentiment analysis"""
        try:
            page = self.session.query(Page).filter(Page.id == page_id).first()
            if not page:
                return None

            content = f"{page.title}\n\n{page.content_text}"

            sentiment_prompt = """
            Analyze the sentiment of the following content. Provide:
            1. Overall sentiment (positive, negative, neutral)
            2. Sentiment score (-1.0 to 1.0, where -1 is very negative, 1 is very positive)
            3. Confidence level (0.0-1.0)
            4. Emotional indicators (anger, fear, joy, sadness, surprise, etc.)
            5. Key phrases that influenced the sentiment

            Format as JSON:
            {
                "overall_sentiment": "neutral",
                "sentiment_score": 0.1,
                "confidence": 0.8,
                "emotional_indicators": {"fear": 0.3, "concern": 0.6},
                "key_phrases": ["concerning development", "positive outlook"]
            }
            """

            sentiment_result = self.analyze_with_ollama(sentiment_prompt, model=SENTIMENT_MODEL, context=content)

            if not sentiment_result:
                return None

            try:
                sentiment_data = json.loads(sentiment_result)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse sentiment JSON for page {page_id}")
                return None

            sentiment_analysis = SentimentAnalysis(
                page_id=page_id,
                overall_sentiment=sentiment_data.get('overall_sentiment', 'neutral'),
                sentiment_score=float(sentiment_data.get('sentiment_score', 0.0)),
                confidence=float(sentiment_data.get('confidence', 0.0)),
                emotional_indicators=sentiment_data.get('emotional_indicators', {}),
                key_phrases=sentiment_data.get('key_phrases', [])
            )

            self.session.add(sentiment_analysis)
            self.session.commit()

            logger.info(f"Created sentiment analysis for page {page_id}")
            return sentiment_analysis

        except Exception as e:
            logger.error(f"Error in sentiment analysis: {e}")
            self.session.rollback()
            return None

    def analyze_topics(self, page_id: int) -> Optional[TopicClustering]:
        """Perform topic modeling and clustering"""
        try:
            page = self.session.query(Page).filter(Page.id == page_id).first()
            if not page:
                return None

            content = f"{page.title}\n\n{page.content_text}"

            topic_prompt = """
            Analyze the main topics and themes in the following content. Provide:
            1. Primary topic (single most dominant theme)
            2. Topic probability (0.0-1.0 confidence in primary topic)
            3. Secondary topics (up to 3 additional themes with probabilities)
            4. Key keywords (5-10 most important terms)
            5. Topic summary (1-2 sentences describing the main theme)

            Format as JSON:
            {
                "primary_topic": "cybersecurity",
                "topic_probability": 0.85,
                "secondary_topics": [{"topic": "data privacy", "probability": 0.6}],
                "keywords": ["encryption", "security", "privacy"],
                "topic_summary": "Discussion of cybersecurity measures and data protection."
            }
            """

            topic_result = self.analyze_with_ollama(topic_prompt, context=content)

            if not topic_result:
                return None

            try:
                topic_data = json.loads(topic_result)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse topic JSON for page {page_id}")
                return None

            # Generate cluster ID based on primary topic
            cluster_id = hashlib.md5(topic_data.get('primary_topic', '').encode()).hexdigest()[:8]

            topic_clustering = TopicClustering(
                page_id=page_id,
                primary_topic=topic_data.get('primary_topic', ''),
                topic_probability=float(topic_data.get('topic_probability', 0.0)),
                secondary_topics=topic_data.get('secondary_topics', []),
                keywords=topic_data.get('keywords', []),
                topic_summary=topic_data.get('topic_summary', ''),
                cluster_id=cluster_id
            )

            self.session.add(topic_clustering)
            self.session.commit()

            logger.info(f"Created topic analysis for page {page_id}")
            return topic_clustering

        except Exception as e:
            logger.error(f"Error in topic analysis: {e}")
            self.session.rollback()
            return None

    def generate_deep_insights(self, research_target_id: int) -> List[DeepInsights]:
        """Generate advanced insights and correlations for a research target"""
        try:
            research_target = self.session.query(ResearchTarget).filter(ResearchTarget.id == research_target_id).first()
            if not research_target:
                return []

            # Get all analyses for this research target
            analyses = self.session.query(ContentAnalysis).filter(
                ContentAnalysis.research_target_id == research_target_id
            ).all()

            if not analyses:
                logger.warning(f"No analyses found for research target {research_target_id}")
                return []

            # Prepare data for insight generation
            analysis_summaries = []
            for analysis in analyses:
                analysis_summaries.append({
                    'page_id': analysis.page_id,
                    'relevance_score': analysis.relevance_score,
                    'summary': analysis.summary,
                    'key_points': analysis.key_points or []
                })

            insight_prompt = f"""
            Based on the following research analyses for the target "{research_target.name}", 
            generate deep insights and correlations. Look for:

            1. PATTERNS AND TRENDS across multiple sources
            2. CORRELATIONS between different pieces of information
            3. ANOMALIES or unexpected findings
            4. RISK ASSESSMENTS for potential concerns
            5. PREDICTIVE INSIGHTS about future developments

            For each insight, provide:
            - Type (correlation, trend, anomaly, prediction)
            - Title (concise description)
            - Description (detailed explanation)
            - Confidence level (0.0-1.0)
            - Risk assessment (low, medium, high, critical)
            - Actionable items (what should be done)

            Research Goal: {research_target.research_goals}
            Keywords: {', '.join(research_target.keywords or [])}

            Format as JSON array of insights.
            """

            insights_result = self.analyze_with_ollama(
                insight_prompt,
                context=json.dumps(analysis_summaries, indent=2)
            )

            if not insights_result:
                return []

            try:
                insights_data = json.loads(insights_result)
                if not isinstance(insights_data, list):
                    return []
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse insights JSON for research target {research_target_id}")
                return []

            insights = []
            for insight_data in insights_data:
                if all(key in insight_data for key in ['type', 'title', 'description']):
                    insight = DeepInsights(
                        research_target_id=research_target_id,
                        insight_type=insight_data.get('type', ''),
                        title=insight_data.get('title', '')[:255],
                        description=insight_data.get('description', ''),
                        confidence_level=float(insight_data.get('confidence_level', 0.0)),
                        risk_assessment=insight_data.get('risk_assessment', 'low'),
                        actionable_items=insight_data.get('actionable_items', []),
                        supporting_evidence=[analysis.page_id for analysis in analyses],
                        metadata=insight_data
                    )
                    insights.append(insight)

            if insights:
                self.session.add_all(insights)
                self.session.commit()
                logger.info(f"Generated {len(insights)} deep insights for research target {research_target_id}")

            return insights

        except Exception as e:
            logger.error(f"Error generating deep insights: {e}")
            self.session.rollback()
            return []

    def _extract_relevance_score(self, analysis_text: str) -> float:
        """Extract relevance score from analysis text"""
        try:
            # Look for relevance patterns in the text
            import re
            patterns = [
                r'relevance[:\s]+(\d+(?:\.\d+)?)/10',
                r'relevance[:\s]+(\d+(?:\.\d+)?)',
                r'rate[:\s]+(\d+(?:\.\d+)?)/10',
                r'score[:\s]+(\d+(?:\.\d+)?)/10'
            ]

            for pattern in patterns:
                match = re.search(pattern, analysis_text.lower())
                if match:
                    score = float(match.group(1))
                    return min(score / 10.0, 1.0) if score > 1.0 else score

            # Default relevance based on analysis length and keywords
            return 0.5  # Default medium relevance

        except Exception:
            return 0.5

    def research_reporting(self, research_target_id: int, output_file: str = None) -> str:
        """Generate comprehensive research report"""
        try:
            research_target = self.session.query(ResearchTarget).filter(ResearchTarget.id == research_target_id).first()
            if not research_target:
                return "Research target not found"

            # Get all related data
            analyses = self.session.query(ContentAnalysis).filter(
                ContentAnalysis.research_target_id == research_target_id
            ).order_by(ContentAnalysis.relevance_score.desc()).all()

            insights = self.session.query(DeepInsights).filter(
                DeepInsights.research_target_id == research_target_id
            ).order_by(DeepInsights.confidence_level.desc()).all()

            # Generate report
            report = f"""
# AI Research Report: {research_target.name}

## Executive Summary
Research Target: {research_target.name}
Generated: {datetime.utcnow().isoformat()}
Total Analyses: {len(analyses)}
Deep Insights: {len(insights)}

## Research Objectives
{research_target.description}

### Research Goals
{research_target.research_goals or 'Not specified'}

### Target Keywords
{', '.join(research_target.keywords or [])}

## Key Findings

### High-Relevance Content
"""

            # Add top analyses
            for analysis in analyses[:5]:  # Top 5 most relevant
                page = self.session.query(Page).filter(Page.id == analysis.page_id).first()
                if page:
                    report += f"""
#### {page.title or 'Untitled'}
- **URL**: {page.url}
- **Relevance Score**: {analysis.relevance_score:.2f}
- **Analysis**: {analysis.summary[:500]}...
"""

            # Add deep insights
            if insights:
                report += "\n## Deep Insights and Correlations\n"
                for insight in insights:
                    report += f"""
### {insight.title}
- **Type**: {insight.insight_type}
- **Confidence**: {insight.confidence_level:.2f}
- **Risk Assessment**: {insight.risk_assessment}
- **Description**: {insight.description}
"""
                    if insight.actionable_items:
                        report += f"- **Action Items**: {', '.join(insight.actionable_items)}\n"

            # Save report if filename provided
            if output_file:
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(report)
                logger.info(f"Research report saved to {output_file}")

            return report

        except Exception as e:
            logger.error(f"Error generating research report: {e}")
            return f"Error generating report: {str(e)}"

    def process_pages_for_research(self, research_target_id: int, max_pages: int = 50):
        """Process pages for comprehensive research analysis"""
        try:
            research_target = self.session.query(ResearchTarget).filter(ResearchTarget.id == research_target_id).first()
            if not research_target:
                logger.error(f"Research target not found: {research_target_id}")
                return

            # Get pages that match research keywords
            keywords = research_target.keywords or []
            pages_query = self.session.query(Page)

            if keywords:
                # Filter pages that contain research keywords
                keyword_filters = []
                for keyword in keywords:
                    keyword_filters.append(Page.content_text.like(f'%{keyword}%'))
                    keyword_filters.append(Page.title.like(f'%{keyword}%'))

                from sqlalchemy import or_
                pages_query = pages_query.filter(or_(*keyword_filters))

            pages = pages_query.order_by(Page.crawled_at.desc()).limit(max_pages).all()

            logger.info(f"Processing {len(pages)} pages for research target: {research_target.name}")

            for i, page in enumerate(pages, 1):
                logger.info(f"Processing page {i}/{len(pages)}: {page.url}")

                # Skip if already analyzed
                existing_analysis = self.session.query(ContentAnalysis).filter(
                    ContentAnalysis.page_id == page.id,
                    ContentAnalysis.research_target_id == research_target_id
                ).first()

                if existing_analysis:
                    logger.info(f"Skipping already analyzed page: {page.id}")
                    continue

                # Perform comprehensive analysis
                analysis = self.ai_content_analysis(page.id, research_target_id)
                if analysis:
                    # Extract entities
                    self.extract_entities_ai(page.id)

                    # Perform sentiment analysis
                    self.perform_sentiment_analysis(page.id)

                    # Analyze topics
                    self.analyze_topics(page.id)

                    # Small delay to avoid overwhelming the AI service
                    time.sleep(1)

            # Generate deep insights after processing all pages
            logger.info("Generating deep insights...")
            insights = self.generate_deep_insights(research_target_id)

            logger.info(f"Research processing complete. Generated {len(insights)} insights.")

        except Exception as e:
            logger.error(f"Error processing pages for research: {e}")

    def close(self):
        """Close database session"""
        if self.session:
            self.session.close()


def main():
    """Main execution function"""
    print("AI-Powered Research Crawler")
    print("=" * 50)

    crawler = AIResearchCrawler()

    try:
        # Example: Create a research target
        research_target = crawler.create_research_target(
            name="Cybersecurity Threat Analysis",
            description="Research emerging cybersecurity threats and vulnerabilities",
            keywords=["cybersecurity", "vulnerability", "exploit", "malware", "breach", "hacking"],
            research_goals="Identify emerging threats, assess risk levels, and provide actionable intelligence"
        )

        if research_target:
            print(f"Created research target: {research_target.name}")

            # Process pages for this research target
            print("Processing pages for research analysis...")
            crawler.process_pages_for_research(research_target.id, max_pages=20)

            # Generate research report
            print("Generating research report...")
            report = crawler.research_reporting(
                research_target.id,
                f"research_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
            )

            print("Research analysis complete!")
            print(f"Report preview:\n{report[:500]}...")

    except Exception as e:
        logger.error(f"Error in main execution: {e}")

    finally:
        crawler.close()


if __name__ == "__main__":
    main()