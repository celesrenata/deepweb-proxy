
import os
import json
import argparse
import logging
from datetime import datetime
from collections import defaultdict
from urllib.parse import urlparse
import re
from ai_analysis import process_with_text_ai, process_with_multimodal_ai

# Set up logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)

# ... (keep the existing imports and logging setup)

def extract_url_depth(url):
    """Extract the depth of a URL based on its path."""
    parsed = urlparse(url)
    path = parsed.path.strip('/')
    depth = len(path.split('/')) if path else 0
    return depth

def summarize_txt_analysis(file_path):
    """Summarize a text-format analysis file, grouping content by URL depth."""
    try:
        logger.info(f"Starting to summarize text file: {file_path}")
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        site_sections = re.split(r'={80}', content)
        site_sections = [s.strip() for s in site_sections if s.strip()]
        
        summary = {
            "file_name": os.path.basename(file_path),
            "total_sites_analyzed": len(site_sections) - 1,
            "depth_summary": defaultdict(lambda: {"pages": 0, "images": 0, "media": 0}),
            "sites": []
        }
        
        logger.info(f"Found {summary['total_sites_analyzed']} sites in the analysis file")
        
        for section in site_sections[1:]:
            site_match = re.search(r'SITE \d+: (.*?)\n', section) or re.search(r'ANALYSIS \d+: (.*?)\n', section)
            
            if site_match:
                site_url = site_match.group(1).strip()
                site_type = re.search(r'Type: (.*?)\n', section)
                site_type = site_type.group(1).strip() if site_type else "unknown"
                
                logger.debug(f"Processing site: {site_url}")
                
                page_sections = re.findall(r'URL: (.*?)\nTitle: (.*?)\nCrawled: (.*?)\nImages: (\d+), Media: (\d+)\nAnalysis:\n(.*?)(?:-{40}|\Z)', 
                                           section, re.DOTALL)
                
                site_depth_summary = defaultdict(lambda: {"pages": 0, "images": 0, "media": 0})
                
                for url, title, crawled, img_count, media_count, analysis in page_sections:
                    depth = extract_url_depth(url)
                    site_depth_summary[depth]["pages"] += 1
                    site_depth_summary[depth]["images"] += int(img_count)
                    site_depth_summary[depth]["media"] += int(media_count)
                    
                    # Use AI to summarize the analysis
                    logger.debug(f"Generating AI summary for page: {url}")
                    ai_summary = process_with_text_ai(analysis, "Summarize this analysis concisely.")
                    
                    summary["depth_summary"][depth]["pages"] += 1
                    summary["depth_summary"][depth]["images"] += int(img_count)
                    summary["depth_summary"][depth]["media"] += int(media_count)
                
                summary["sites"].append({
                    "url": site_url,
                    "type": site_type,
                    "depth_summary": dict(site_depth_summary),
                    "ai_summary": ai_summary
                })
        
        logger.info(f"Successfully summarized text file: {file_path}")
        return summary
    
    except Exception as e:
        logger.error(f"Error summarizing text file {file_path}: {e}")
        return {"error": str(e)}

def summarize_json_analysis(file_path):
    """Summarize a JSON-format analysis file, grouping by URL depth."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        summary = {
            "file_name": os.path.basename(file_path),
            "total_sites_analyzed": len(data),
            "depth_summary": defaultdict(lambda: {"pages": 0, "images": 0, "media": 0}),
            "sites": []
        }
        
        for site_data in data:
            site_url = site_data.get("site_url", "Unknown")
            site_type = site_data.get("site_type", "unknown")
            
            site_depth_summary = defaultdict(lambda: {"pages": 0, "images": 0, "media": 0})
            
            for page in site_data.get("page_analyses", []):
                depth = extract_url_depth(page.get("url", ""))
                site_depth_summary[depth]["pages"] += 1
                site_depth_summary[depth]["images"] += page.get("image_count", 0)
                site_depth_summary[depth]["media"] += page.get("media_count", 0)
                
                summary["depth_summary"][depth]["pages"] += 1
                summary["depth_summary"][depth]["images"] += page.get("image_count", 0)
                summary["depth_summary"][depth]["media"] += page.get("media_count", 0)
            
            # Use AI to summarize the site analysis
            site_summary = site_data.get("site_summary", "")
            ai_summary = process_with_text_ai(site_summary, "Summarize this site analysis concisely.")
            
            summary["sites"].append({
                "url": site_url,
                "type": site_type,
                "depth_summary": dict(site_depth_summary),
                "ai_summary": ai_summary
            })
        
        return summary
    
    except Exception as e:
        logger.error(f"Error summarizing JSON file {file_path}: {e}")
        return {"error": str(e)}

def generate_summary_report(summary, output_format="txt"):
    """Generate a summary report from the analysis data, organized by URL depth."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_filename = f"depth_analysis_summary_{timestamp}.{output_format}"
    
    if output_format == "json":
        with open(output_filename, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2)
    else:
        with open(output_filename, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("DEPTH-BASED ANALYSIS SUMMARY REPORT\n")
            f.write("=" * 80 + "\n\n")
            
            f.write(f"Original File: {summary['file_name']}\n")
            f.write(f"Sites Analyzed: {summary['total_sites_analyzed']}\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            f.write("OVERALL DEPTH SUMMARY:\n")
            for depth, stats in sorted(summary['depth_summary'].items()):
                f.write(f"  Depth {depth}: {stats['pages']} pages, {stats['images']} images, {stats['media']} media files\n")
            
            f.write("\n" + "=" * 80 + "\n\n")
            
            for i, site in enumerate(summary['sites'], 1):
                f.write(f"SITE {i}: {site['url']}\n")
                f.write("-" * 60 + "\n")
                f.write(f"Type: {site['type']}\n\n")
                
                f.write("Depth Summary:\n")
                for depth, stats in sorted(site['depth_summary'].items()):
                    f.write(f"  Depth {depth}: {stats['pages']} pages, {stats['images']} images, {stats['media']} media files\n")
                
                f.write("\nAI-Generated Summary:\n")
                f.write(f"{site['ai_summary']}\n\n")
                
                f.write("=" * 80 + "\n\n")
    
    logger.info(f"Depth-based summary report generated: {output_filename}")
    return output_filename

# ... (keep the existing find_analysis_files function)

def find_analysis_files(directory):
    """Find all analysis files (txt and json) in the given directory."""
    analysis_files = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(('.txt', '.json')) and 'analysis' in file.lower():
                analysis_files.append(os.path.join(root, file))
    return analysis_files

def main():
    """Main entry point with command-line arguments."""
    parser = argparse.ArgumentParser(description="Summarize comprehensive site analysis output by URL depth")
    parser.add_argument('--file', type=str, help="Path to the analysis file to summarize")
    parser.add_argument('--dir', type=str, default=".", help="Directory to search for analysis files")
    parser.add_argument('--format', type=str, default="txt", choices=["txt", "json"], 
                        help="Output format for the summary")
    
    args = parser.parse_args()
    
    print(f"Depth-Based Analysis Summarizer")
    print(f"===============================")
    
    if args.file:
        if args.file.endswith('.json'):
            summary = summarize_json_analysis(args.file)
        else:
            summary = summarize_txt_analysis(args.file)
        
        if 'error' in summary:
            print(f"Error processing file: {summary['error']}")
        else:
            output_file = generate_summary_report(summary, args.format)
            print(f"Summary report generated: {output_file}")
    
    else:
        analysis_files = find_analysis_files(args.dir)
        if not analysis_files:
            print(f"No analysis files found in directory: {args.dir}")
            return
        
        print(f"Found {len(analysis_files)} analysis files:")
        for i, file in enumerate(analysis_files, 1):
            print(f"{i}. {file}")
        
        choice = input("Enter the number of the file to summarize (or 'all' for all files): ")
        
        if choice.lower() == 'all':
            for file in analysis_files:
                if file.endswith('.json'):
                    summary = summarize_json_analysis(file)
                else:
                    summary = summarize_txt_analysis(file)
                
                if 'error' in summary:
                    print(f"Error processing {file}: {summary['error']}")
                else:
                    output_file = generate_summary_report(summary, args.format)
                    print(f"Summary report generated for {file}: {output_file}")
        else:
            try:
                file_index = int(choice) - 1
                if 0 <= file_index < len(analysis_files):
                    file = analysis_files[file_index]
                    if file.endswith('.json'):
                        summary = summarize_json_analysis(file)
                    else:
                        summary = summarize_txt_analysis(file)
                    
                    if 'error' in summary:
                        print(f"Error processing {file}: {summary['error']}")
                    else:
                        output_file = generate_summary_report(summary, args.format)
                        print(f"Summary report generated: {output_file}")
                else:
                    print("Invalid file number.")
            except ValueError:
                print("Invalid input. Please enter a number or 'all'.")
def summarize_txt_analysis(file_path):
    """
    Summarize a text-format analysis file, grouping content by URL depth.

    This function reads a text file containing website analysis data, processes its content,
    and generates a summary grouped by URL depth. It extracts information about sites,
    pages, images, and media files, and uses AI to generate concise summaries for each site.

    Parameters:
    file_path (str): The path to the text file containing the analysis data.

    Returns:
    dict: A dictionary containing the summarized data with the following structure:
        - file_name (str): The name of the analyzed file.
        - total_sites_analyzed (int): The total number of sites analyzed.
        - depth_summary (dict): A summary of pages, images, and media files by URL depth.
        - sites (list): A list of dictionaries, each containing data for a single site:
            - url (str): The URL of the site.
            - type (str): The type of the site.
            - depth_summary (dict): A summary of pages, images, and media files by URL depth for this site.
            - ai_summary (str): An AI-generated summary of the site's analysis.

    If an error occurs during processing, it returns a dictionary with a single key 'error'
    containing the error message as a string.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        site_sections = re.split(r'={80}', content)
        site_sections = [s.strip() for s in site_sections if s.strip()]
        
        summary = {
            "file_name": os.path.basename(file_path),
            "total_sites_analyzed": len(site_sections) - 1,
            "depth_summary": defaultdict(lambda: {"pages": 0, "images": 0, "media": 0}),
            "sites": []
        }
        
        for section in site_sections[1:]:
            site_match = re.search(r'SITE \d+: (.*?)\n', section) or re.search(r'ANALYSIS \d+: (.*?)\n', section)
            
            if site_match:
                site_url = site_match.group(1).strip()
                site_type = re.search(r'Type: (.*?)\n', section)
                site_type = site_type.group(1).strip() if site_type else "unknown"
                
                page_sections = re.findall(r'URL: (.*?)\nTitle: (.*?)\nCrawled: (.*?)\nImages: (\d+), Media: (\d+)\nAnalysis:\n(.*?)(?:-{40}|\Z)', 
                                           section, re.DOTALL)
                
                site_depth_summary = defaultdict(lambda: {"pages": 0, "images": 0, "media": 0})
                
                for url, title, crawled, img_count, media_count, analysis in page_sections:
                    depth = extract_url_depth(url)
                    site_depth_summary[depth]["pages"] += 1
                    site_depth_summary[depth]["images"] += int(img_count)
                    site_depth_summary[depth]["media"] += int(media_count)
                    
                    # Use AI to summarize the analysis
                    ai_summary = process_with_text_ai(analysis, "Summarize this analysis concisely.")
                    
                    summary["depth_summary"][depth]["pages"] += 1
                    summary["depth_summary"][depth]["images"] += int(img_count)
                    summary["depth_summary"][depth]["media"] += int(media_count)
                
                summary["sites"].append({
                    "url": site_url,
                    "type": site_type,
                    "depth_summary": dict(site_depth_summary),
                    "ai_summary": ai_summary
                })
        
        return summary
    
    except Exception as e:
        logger.error(f"Error summarizing text file {file_path}: {e}")
        return {"error": str(e)}                
if __name__ == "__main__":
    main()