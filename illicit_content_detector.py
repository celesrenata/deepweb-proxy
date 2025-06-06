# illicit_content_detector.py

import os
import logging
import pandas as pd
from datetime import datetime
import time
from sqlalchemy import select
from db_models import get_db_session, MediaFile
import requests
import re
import argparse

global OUTPUT_DIR

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration - define global variables at the start
OLLAMA_ENDPOINT = os.getenv("OLLAMA_ENDPOINT", "http://10.1.1.12:2701/api/generate")
AI_MODEL = os.getenv("AI_MODEL", "llama3.1:8b")  # More efficient text model for content review
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "./reports")

# Keywords for potential illicit content detection
# These lists are used for initial filtering - the AI will do a more thorough analysis
ILLICIT_KEYWORDS = {
    'child_exploitation': [
        'child porn', 'childporn', 'cp', 'underage', 'minor', 'lolita', 'preteen',
        'pedo', 'kid', 'young girl', 'young boy', 'infant', 'toddler', 'teen', 'jb',
        'baby', 'elementary', 'middle school', 'prepubescent'
    ],
    'weapons_violence': [
        'gun', 'rifle', 'firearm', 'weapon', 'ammunition', 'explosive', 'bomb',
        'shooting', 'terrorism', 'terrorist', 'attack', 'assault rifle',
        'homemade explosive', 'murder', 'kill', 'assassinate', 'massacre'
    ],
    'drugs_substances': [
        'cocaine', 'heroin', 'meth', 'fentanyl', 'drug lab', 'drug manufacturing',
        'amphetamine', 'synthetic', 'opioid', 'illegal substance', 'narcotic',
        'pill press', 'drug production', 'darknet market'
    ],
    'explicit_adult': [
        'explicit', 'nude', 'naked', 'sex', 'pornography', 'sexual', 'obscene',
        'erotic', 'genital', 'exposed', 'intimate', 'indecent', 'lewd'
    ]
}


def get_all_media_with_descriptions():
    """Fetch all media files that have descriptions."""
    session = get_db_session()
    try:
        # Query for media files that have non-null descriptions
        query = select(MediaFile).where(
            MediaFile.description.is_not(None) &
            (MediaFile.description != '')
        )

        media_files = session.execute(query).scalars().all()
        logger.info(f"Found {len(media_files)} media files with descriptions")
        return media_files
    except Exception as e:
        logger.error(f"Error fetching media files: {e}")
        return []
    finally:
        session.close()


def initial_keyword_match(description, keywords_dict):
    """
    Perform initial keyword matching to identify potentially problematic content.
    Returns a dictionary with categories and their match counts.
    """
    if not description:
        return {}

    # Convert to lowercase for case-insensitive matching
    description_lower = description.lower()

    # Check for keywords in each category
    matches = {}
    for category, keywords in keywords_dict.items():
        category_count = 0
        category_matches = []

        for keyword in keywords:
            # Use word boundary to prevent partial matches (e.g., "kill" in "skill")
            pattern = r'\b' + re.escape(keyword) + r'\b'
            matches_found = re.findall(pattern, description_lower)

            if matches_found:
                category_count += len(matches_found)
                category_matches.extend(matches_found)

        if category_count > 0:
            matches[category] = {
                'count': category_count,
                'matches': list(set(category_matches))  # Remove duplicates
            }

    return matches


def ai_content_review(description, media_file=None):
    """
    Use AI to analyze the description and determine if it contains illicit content.
    Returns a confidence score and explanation.
    """
    if not description or len(description) < 10:  # Skip very short descriptions
        return {
            'is_illicit': False,
            'confidence': 0,
            'explanation': "Description too short for analysis",
            'categories': []
        }

    # Construct a prompt for the AI
    prompt = f"""
As a content moderation system, analyze the following image description to determine if it describes potentially illicit or harmful content. 
Consider these categories of concern:
1. Child exploitation or abuse
2. Extreme violence, terrorism, or weapons
3. Illegal drugs or substances
4. Explicit adult content
5. Other harmful activities

Image description: "{description}"

For your answer, use this exact format:
RATING: [0-100]
CATEGORIES: [list applicable categories, separated by commas]
EXPLANATION: [provide a brief explanation of your rating]

The rating should reflect the likelihood that the content is problematic on a scale of 0-100, where:
0-20: Very unlikely to be problematic
21-40: Possibly concerning but likely benign
41-60: Moderately concerning
61-80: Highly concerning 
81-100: Almost certainly illicit content
"""

    try:
        # Call the Ollama API
        response = requests.post(
            OLLAMA_ENDPOINT,
            json={"model": AI_MODEL, "prompt": prompt, "stream": False},
            timeout=30
        )

        if response.status_code == 200:
            ai_response = response.json().get("response", "")

            # Parse the structured response
            rating_match = re.search(r'RATING:\s*(\d+)', ai_response)
            categories_match = re.search(r'CATEGORIES:\s*(.*?)(?:\n|$)', ai_response)
            explanation_match = re.search(r'EXPLANATION:\s*(.*?)(?:\n|$)', ai_response)

            confidence = int(rating_match.group(1)) if rating_match else 0
            categories_text = categories_match.group(1) if categories_match else ""
            explanation = explanation_match.group(1) if explanation_match else "No explanation provided"

            categories = [cat.strip() for cat in categories_text.split(',') if cat.strip()]

            return {
                'is_illicit': confidence > 40,  # Consider scores > 40 as potentially problematic
                'confidence': confidence,
                'explanation': explanation,
                'categories': categories
            }
        else:
            logger.error(f"AI API error: {response.status_code} - {response.text}")
            return {
                'is_illicit': False,
                'confidence': 0,
                'explanation': f"API error: {response.status_code}",
                'categories': []
            }

    except Exception as e:
        logger.error(f"Error in AI content review: {e}")
        return {
            'is_illicit': False,
            'confidence': 0,
            'explanation': f"Error: {str(e)}",
            'categories': []
        }


def scan_all_descriptions(threshold=20):
    """
    Scan all descriptions in the database for potentially illicit content.
    Args:
        threshold: Minimum confidence score to include in the report
    """
    start_time = time.time()
    media_files = get_all_media_with_descriptions()

    if not media_files:
        logger.warning("No media files with descriptions found")
        return False

    # Process all files
    results = []
    total_files = len(media_files)

    for i, media_file in enumerate(media_files):
        if i % 100 == 0:  # Progress reporting
            logger.info(f"Processing {i + 1}/{total_files} ({(i + 1) / total_files * 100:.1f}%)")

        description = media_file.description

        # Skip file if no useful description
        if not description or description.strip() == "":
            continue

        # Initial keyword scan
        keyword_matches = initial_keyword_match(description, ILLICIT_KEYWORDS)

        # Only do AI review if there are keyword matches or randomly sample 5%
        if keyword_matches or (hash(media_file.id) % 20 == 0):
            ai_result = ai_content_review(description, media_file)

            # If AI thinks it's problematic or keyword matches have high counts, add to results
            total_keyword_matches = sum(cat_data['count'] for cat_data in keyword_matches.values())
            keyword_categories = list(keyword_matches.keys())

            # If the content exceeds our threshold from either method, report it
            if ai_result['confidence'] >= threshold or total_keyword_matches >= 2:
                results.append({
                    'media_id': media_file.id,
                    'filename': media_file.filename,
                    'url': media_file.url,
                    'file_type': media_file.file_type,
                    'description': description,
                    'keyword_matches': keyword_matches,
                    'keyword_count': total_keyword_matches,
                    'keyword_categories': keyword_categories,
                    'ai_confidence': ai_result['confidence'],
                    'ai_explanation': ai_result['explanation'],
                    'ai_categories': ai_result['categories'],
                    'final_score': max(ai_result['confidence'], total_keyword_matches * 10)
                })

        # Slow down to avoid overwhelming the API
        time.sleep(0.1)

    # Sort results by confidence score (highest first)
    sorted_results = sorted(results, key=lambda x: x['final_score'], reverse=True)

    # Generate report
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = os.path.join(OUTPUT_DIR, f"illicit_content_report_{timestamp}.csv")

    # Convert to DataFrame for easy CSV export
    df = pd.DataFrame(sorted_results)

    # Save to CSV
    if len(df) > 0:
        # Ensure the keyword data is properly formatted for CSV
        df['keyword_matches_str'] = df['keyword_matches'].apply(lambda x: str(x))
        df['keyword_categories_str'] = df['keyword_categories'].apply(lambda x: ', '.join(x) if x else "")
        df['ai_categories_str'] = df['ai_categories'].apply(lambda x: ', '.join(x) if x else "")

        # Select columns for the CSV (excluding complex objects that can't be directly serialized)
        columns_for_csv = [
            'media_id', 'filename', 'url', 'file_type', 'description',
            'keyword_count', 'keyword_categories_str',
            'ai_confidence', 'ai_explanation', 'ai_categories_str', 'final_score'
        ]

        df[columns_for_csv].to_csv(report_file, index=False)

    # Also generate a summary HTML report
    html_report_file = os.path.join(OUTPUT_DIR, f"illicit_content_report_{timestamp}.html")

    # Create a nicer HTML report with filtering capabilities
    html_report = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Illicit Content Report - {timestamp}</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            h1, h2 {{ color: #333; }}
            .summary {{ background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
            .filters {{ margin-bottom: 20px; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            th {{ background-color: #f2f2f2; }}
            tr:nth-child(even) {{ background-color: #f9f9f9; }}
            .high-concern {{ background-color: #ffcccc; }}
            .medium-concern {{ background-color: #ffffcc; }}
            .low-concern {{ background-color: #e6f3ff; }}
        </style>
        <script>
            function filterTable() {{
                let minScore = document.getElementById('minScore').value;
                let category = document.getElementById('category').value.toLowerCase();

                let rows = document.querySelectorAll('#resultsTable tbody tr');
                rows.forEach(row => {{
                    let score = parseInt(row.querySelector('td:nth-child(10)').textContent);
                    let cats = row.querySelector('td:nth-child(7)').textContent.toLowerCase() + ' ' + 
                               row.querySelector('td:nth-child(9)').textContent.toLowerCase();

                    if (score >= minScore && (category === '' || cats.includes(category))) {{
                        row.style.display = '';
                    }} else {{
                        row.style.display = 'none';
                    }}
                }});

                updateCount();
            }}

            function updateCount() {{
                let visibleRows = document.querySelectorAll('#resultsTable tbody tr:not([style*="display: none"])').length;
                document.getElementById('visibleCount').textContent = visibleRows;
            }}
        </script>
    </head>
    <body>
        <h1>Illicit Content Detection Report</h1>
        <div class="summary">
            <h2>Summary</h2>
            <p>Report generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
            <p>Total media files scanned: {total_files}</p>
            <p>Potentially problematic content identified: {len(sorted_results)}</p>
            <p>High concern items (score > 70): {sum(1 for r in sorted_results if r['final_score'] > 70)}</p>
            <p>Medium concern items (score 40-70): {sum(1 for r in sorted_results if 40 <= r['final_score'] <= 70)}</p>
            <p>Low concern items (score < 40): {sum(1 for r in sorted_results if r['final_score'] < 40)}</p>
        </div>

        <div class="filters">
            <h2>Filters</h2>
            <label for="minScore">Minimum Score:</label>
            <input type="number" id="minScore" value="0" min="0" max="100" step="5" onchange="filterTable()">

            <label for="category" style="margin-left: 20px;">Category Contains:</label>
            <input type="text" id="category" onkeyup="filterTable()">

            <p>Showing <span id="visibleCount">{len(sorted_results)}</span> results</p>
        </div>

        <table id="resultsTable">
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Filename</th>
                    <th>Type</th>
                    <th>Description</th>
                    <th>Keyword Count</th>
                    <th>Keyword Categories</th>
                    <th>AI Score</th>
                    <th>AI Explanation</th>
                    <th>AI Categories</th>
                    <th>Final Score</th>
                </tr>
            </thead>
            <tbody>
    """

    for result in sorted_results:
        concern_class = ""
        if result['final_score'] > 70:
            concern_class = "high-concern"
        elif result['final_score'] >= 40:
            concern_class = "medium-concern"
        else:
            concern_class = "low-concern"

        # Truncate description if too long
        description = result['description']
        if len(description) > 200:
            description = description[:197] + "..."

        # Format as an HTML row
        html_report += f"""
                <tr class="{concern_class}">
                    <td>{result['media_id']}</td>
                    <td>{result['filename'] or 'Unknown'}</td>
                    <td>{result['file_type'] or 'Unknown'}</td>
                    <td>{description}</td>
                    <td>{result['keyword_count']}</td>
                    <td>{', '.join(result['keyword_categories']) if result['keyword_categories'] else ''}</td>
                    <td>{result['ai_confidence']}</td>
                    <td>{result['ai_explanation']}</td>
                    <td>{', '.join(result['ai_categories']) if 'ai_categories' in result and result['ai_categories'] else ''}</td>
                    <td>{result['final_score']}</td>
                </tr>
        """

    html_report += """
            </tbody>
        </table>
        <script>
            // Initialize counts
            updateCount();
        </script>
    </body>
    </html>
    """

    # Save the HTML report
    with open(html_report_file, 'w', encoding='utf-8') as f:
        f.write(html_report)

    # Also create a high-priority JSON report for items with high scores
    high_priority_results = [r for r in sorted_results if r['final_score'] >= 70]
    if high_priority_results:
        json_report_file = os.path.join(OUTPUT_DIR, f"high_priority_report_{timestamp}.json")

        # Convert complex objects to strings for JSON serialization
        for result in high_priority_results:
            result['keyword_matches'] = str(result['keyword_matches'])
            result['keyword_categories'] = list(result['keyword_categories'])

        import json
        with open(json_report_file, 'w', encoding='utf-8') as f:
            json.dump(high_priority_results, f, indent=2)

    end_time = time.time()
    duration = end_time - start_time

    logger.info(f"Scan completed in {duration:.2f} seconds")
    logger.info(f"Found {len(sorted_results)} potentially problematic descriptions")
    logger.info(f"Reports saved to {report_file} and {html_report_file}")

    if high_priority_results:
        logger.warning(f"Found {len(high_priority_results)} HIGH PRIORITY items requiring immediate review")

    return True


def main():
    """Main entry point with command-line arguments."""
    # Declare global variables at the beginning of the function
    global OUTPUT_DIR

    parser = argparse.ArgumentParser(description="Scan image descriptions for potentially illicit content")
    parser.add_argument('--threshold', type=int, default=20,
                        help="Minimum confidence score (0-100) to include in report")
    parser.add_argument('--output-dir', type=str, default=OUTPUT_DIR, help="Directory to save reports")

    args = parser.parse_args()

    # Now assign to the global variable
    OUTPUT_DIR = args.output_dir

    print(f"Illicit Content Detector")
    print(f"=======================")
    print(f"This tool scans all image descriptions for potentially illicit content.")
    print(f"Threshold: {args.threshold}")
    print(f"Output directory: {OUTPUT_DIR}")
    print()

    confirm = input("Do you want to proceed with the scan? (y/n): ")
    if confirm.lower() != 'y':
        print("Scan canceled.")
        return

    # Create output directory if it doesn't exist
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    print(f"Starting scan...")
    success = scan_all_descriptions(threshold=args.threshold)

    if success:
        print(f"Scan completed successfully. Check the output directory for reports.")
    else:
        print(f"Scan failed. Check the logs for details.")


if __name__ == "__main__":
    main()