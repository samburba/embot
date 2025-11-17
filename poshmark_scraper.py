#!/usr/bin/env python3
"""
Poshmark Closet Scraper
Scrapes listing information from a Poshmark closet page.
"""

import requests
from bs4 import BeautifulSoup
import json
import time
from typing import List, Dict, Optional
import re
import html
import os
from urllib.parse import urlparse


class PoshmarkScraper:
    def __init__(self, closet_username: str, s3_bucket: Optional[str] = None, s3_prefix: Optional[str] = None):
        """
        Initialize the scraper with a Poshmark closet username.
        
        Args:
            closet_username: The username of the Poshmark closet (e.g., 'emily2636')
            s3_bucket: Optional S3 bucket name for storing listings
            s3_prefix: Optional S3 prefix/path for storing listings (default: closet username)
        """
        self.closet_username = closet_username
        self.base_url = f"https://poshmark.com/closet/{closet_username}?availability=available"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        })
        
        # S3 configuration
        self.s3_bucket = s3_bucket or os.getenv('S3_BUCKET')
        self.s3_prefix = s3_prefix or os.getenv('S3_PREFIX') or closet_username
        self.s3_client = None
        
        if self.s3_bucket:
            try:
                import boto3
                self.s3_client = boto3.client('s3')
                print(f"S3 enabled: bucket={self.s3_bucket}, prefix={self.s3_prefix}")
            except ImportError:
                print("Warning: boto3 not available, S3 functionality disabled")
                self.s3_bucket = None
            except Exception as e:
                print(f"Warning: Could not initialize S3 client: {e}")
                self.s3_bucket = None
    
    def fetch_page(self, url: Optional[str] = None) -> Optional[BeautifulSoup]:
        """
        Fetch the HTML content of the page.
        
        Args:
            url: Optional URL to fetch. Defaults to the closet URL.
            
        Returns:
            BeautifulSoup object or None if request fails
        """
        if url is None:
            url = self.base_url
        
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            return BeautifulSoup(response.content, 'html.parser')
        except requests.RequestException as e:
            print(f"Error fetching page: {e}")
            return None
    
    def extract_listing_info(self, listing_element) -> Dict:
        """
        Extract information from a single listing element.
        
        Args:
            listing_element: BeautifulSoup element containing listing data
            
        Returns:
            Dictionary with listing information
        """
        listing_info = {
            'title': '',
            'price': '',
            'original_price': '',
            'size': '',
            'brand': '',
            'condition': '',
            'likes': 0,
            'comments': 0,
            'link': ''
        }
        
        # Extract title
        title_elem = listing_element.find('a', class_=re.compile(r'.*title.*', re.I))
        if not title_elem:
            title_elem = listing_element.find('a', href=re.compile(r'/listing/'))
        if title_elem:
            listing_info['title'] = title_elem.get_text(strip=True)
            listing_info['link'] = title_elem.get('href', '')
            if listing_info['link'] and not listing_info['link'].startswith('http'):
                listing_info['link'] = f"https://poshmark.com{listing_info['link']}"
        
        # Extract price information
        price_elem = listing_element.find(string=re.compile(r'\$[\d,]+'))
        if price_elem:
            prices = re.findall(r'\$([\d,]+)', price_elem)
            if prices:
                listing_info['price'] = prices[0].replace(',', '')
                if len(prices) > 1:
                    listing_info['original_price'] = prices[1].replace(',', '')
        
        # Extract size
        size_elem = listing_element.find(string=re.compile(r'Size:\s*', re.I))
        if size_elem:
            size_text = size_elem.find_next(string=True)
            if size_text:
                listing_info['size'] = size_text.strip()
        
        # Extract brand
        brand_elem = listing_element.find(string=re.compile(r'Brand|Size', re.I))
        if brand_elem:
            # Brand might be near the size or in a separate element
            brand_text = listing_element.find_all(string=True)
            for text in brand_text:
                if text.strip() and not text.strip().startswith(('$', 'Size:', 'NWT')):
                    # This is a heuristic - might need adjustment
                    if len(text.strip()) > 2 and text.strip() not in listing_info['title']:
                        listing_info['brand'] = text.strip()
                        break
        
        # Extract condition (NWT, Flawed, etc.)
        condition_keywords = ['NWT', 'Flawed', 'Play Condition', 'New', 'Used']
        for keyword in condition_keywords:
            if keyword in listing_element.get_text():
                listing_info['condition'] = keyword
                break
        
        # Extract engagement (likes, comments)
        likes_elem = listing_element.find(string=re.compile(r'\d+\s*(like|comment)', re.I))
        if likes_elem:
            numbers = re.findall(r'(\d+)', likes_elem)
            if numbers:
                listing_info['likes'] = int(numbers[0])
        
        return listing_info
    
    def debug_page_structure(self, output_file: str = "debug_page_info.txt"):
        """
        Debug function to extract information about how Poshmark loads listings.
        Saves useful debugging info to a file.
        """
        response = self.session.get(self.base_url, timeout=10)
        if not response.ok:
            print("Failed to fetch page for debugging")
            return
        
        html_text = response.text
        soup = BeautifulSoup(response.content, 'html.parser')
        
        debug_info = []
        debug_info.append("=" * 80)
        debug_info.append("POSHMARK PAGE DEBUG INFO")
        debug_info.append("=" * 80)
        debug_info.append(f"\nURL: {self.base_url}")
        debug_info.append(f"Response Status: {response.status_code}")
        debug_info.append(f"Content Length: {len(html_text)}")
        
        # Find all script tags and look for API endpoints, JSON data, etc.
        debug_info.append("\n" + "=" * 80)
        debug_info.append("SCRIPT TAGS ANALYSIS")
        debug_info.append("=" * 80)
        
        scripts = soup.find_all('script')
        for i, script in enumerate(scripts):
            if script.string:
                script_text = script.string
                # Look for API endpoints
                api_patterns = [
                    r'["\']([^"\']*api[^"\']*closet[^"\']*)["\']',
                    r'["\']([^"\']*api[^"\']*listing[^"\']*)["\']',
                    r'url["\']?\s*[:=]\s*["\']([^"\']*api[^"\']*)["\']',
                    r'endpoint["\']?\s*[:=]\s*["\']([^"\']*)["\']',
                ]
                
                for pattern in api_patterns:
                    matches = re.findall(pattern, script_text, re.I)
                    if matches:
                        debug_info.append(f"\nScript {i} - Found API patterns:")
                        for match in set(matches[:10]):  # Limit to first 10
                            debug_info.append(f"  - {match}")
                
                # Look for pagination/page size info
                page_size_matches = re.findall(r'(?:page[_-]?size|per[_-]?page|limit)\s*[:=]\s*(\d+)', script_text, re.I)
                if page_size_matches:
                    debug_info.append(f"\nScript {i} - Page size info: {set(page_size_matches)}")
                
                # Look for total count
                total_matches = re.findall(r'(?:total|count|size)\s*[:=]\s*(\d+)', script_text, re.I)
                if total_matches:
                    debug_info.append(f"Script {i} - Total/count info: {set(total_matches[:5])}")
                
                # Look for window variables with data
                window_vars = re.findall(r'window\.([A-Z_][A-Z0-9_]*)\s*=', script_text)
                if window_vars:
                    debug_info.append(f"Script {i} - Window variables: {set(window_vars[:10])}")
        
        # Look for data attributes that might contain listing info
        debug_info.append("\n" + "=" * 80)
        debug_info.append("DATA ATTRIBUTES")
        debug_info.append("=" * 80)
        
        all_data_attrs = set()
        # Find all elements and check their attributes
        for elem in soup.find_all(True):  # True means all tags
            if hasattr(elem, 'attrs') and elem.attrs:
                for attr in elem.attrs:
                    if attr.startswith('data-'):
                        all_data_attrs.add(attr)
        
        debug_info.append(f"Found data attributes: {sorted(list(all_data_attrs))[:20]}")
        
        # Look for listing-related data attributes
        listing_elements = soup.find_all(attrs={'data-listing-id': True}) or \
                          soup.find_all(attrs={'data-et-prop-listing_id': True}) or \
                          soup.find_all(attrs={'data-listing': True})
        
        if listing_elements:
            debug_info.append(f"\nFound {len(listing_elements)} elements with listing data attributes")
            sample = listing_elements[0]
            debug_info.append(f"Sample element attributes: {list(sample.attrs.keys())[:10]}")
        
        # Look for JSON-LD or other structured data
        debug_info.append("\n" + "=" * 80)
        debug_info.append("STRUCTURED DATA")
        debug_info.append("=" * 80)
        
        json_ld = soup.find_all('script', type='application/ld+json')
        if json_ld:
            debug_info.append(f"Found {len(json_ld)} JSON-LD scripts")
        
        # Look for common pagination patterns
        debug_info.append("\n" + "=" * 80)
        debug_info.append("PAGINATION ELEMENTS")
        debug_info.append("=" * 80)
        
        pagination_links = soup.find_all('a', href=re.compile(r'[?&](?:page|offset|max_id|cursor)='))
        if pagination_links:
            debug_info.append(f"Found {len(pagination_links)} pagination links")
            for link in pagination_links[:5]:
                debug_info.append(f"  - {link.get('href')}")
        
        # Count listings found
        listing_urls = re.findall(r'/listing/[^"\'<>\s]+', html_text)
        debug_info.append("\n" + "=" * 80)
        debug_info.append("LISTING URLS FOUND")
        debug_info.append("=" * 80)
        debug_info.append(f"Total unique listing URLs in HTML: {len(set(listing_urls))}")
        debug_info.append(f"Sample URLs (first 5):")
        for url in list(set(listing_urls))[:5]:
            debug_info.append(f"  - {url}")
        
        # Look for "load more" or infinite scroll indicators
        debug_info.append("\n" + "=" * 80)
        debug_info.append("INFINITE SCROLL / LOAD MORE")
        debug_info.append("=" * 80)
        
        load_more = soup.find_all(string=re.compile(r'load\s+more|show\s+more|next\s+page', re.I))
        if load_more:
            debug_info.append(f"Found 'load more' text: {len(load_more)} instances")
        
        # Extract window.__INITIAL_STATE__ data
        debug_info.append("\n" + "=" * 80)
        debug_info.append("WINDOW.__INITIAL_STATE__ ANALYSIS")
        debug_info.append("=" * 80)
        
        for script in scripts:
            if script.string and '__INITIAL_STATE__' in script.string:
                script_text = script.string
                # Try to extract the JSON object
                state_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.+?});', script_text, re.DOTALL)
                if state_match:
                    try:
                        state_json = json.loads(state_match.group(1))
                        debug_info.append("Found __INITIAL_STATE__ JSON data")
                        # Look for listing-related keys
                        def find_listing_keys(obj, path="", depth=0):
                            if depth > 5:  # Limit recursion
                                return
                            if isinstance(obj, dict):
                                for key, value in obj.items():
                                    if 'listing' in key.lower() or 'closet' in key.lower():
                                        debug_info.append(f"  Found key: {path}.{key} (type: {type(value).__name__})")
                                        if isinstance(value, (list, dict)) and len(str(value)) < 500:
                                            debug_info.append(f"    Value preview: {str(value)[:200]}")
                                    find_listing_keys(value, f"{path}.{key}" if path else key, depth+1)
                            elif isinstance(obj, list) and len(obj) > 0:
                                find_listing_keys(obj[0], f"{path}[0]", depth+1)
                        
                        find_listing_keys(state_json)
                    except Exception as e:
                        debug_info.append(f"Could not parse __INITIAL_STATE__: {e}")
        
        # Look for GraphQL or API endpoints in scripts
        debug_info.append("\n" + "=" * 80)
        debug_info.append("API ENDPOINTS SEARCH")
        debug_info.append("=" * 80)
        
        api_endpoints = set()
        for script in scripts:
            if script.string:
                # Look for GraphQL endpoints
                graphql_matches = re.findall(r'["\']([^"\']*graphql[^"\']*)["\']', script.string, re.I)
                for match in graphql_matches:
                    api_endpoints.add(match)
                
                # Look for /api/ endpoints
                api_matches = re.findall(r'["\']([^"\']*/api/[^"\']*)["\']', script.string, re.I)
                for match in api_matches:
                    api_endpoints.add(match)
                
                # Look for fetch/axios calls
                fetch_matches = re.findall(r'(?:fetch|axios|request)\(["\']([^"\']+)["\']', script.string, re.I)
                for match in fetch_matches:
                    if '/api/' in match or 'graphql' in match.lower():
                        api_endpoints.add(match)
        
        if api_endpoints:
            debug_info.append("Found potential API endpoints:")
            for endpoint in sorted(list(api_endpoints))[:20]:
                debug_info.append(f"  - {endpoint}")
        else:
            debug_info.append("No API endpoints found in scripts")
        
        # Save to file
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(debug_info))
        
        print(f"\nDebug information saved to {output_file}")
        print("\nKey findings:")
        print(f"  - Listing URLs in HTML: {len(set(listing_urls))}")
        print(f"  - Script tags: {len(scripts)}")
        print(f"  - Data attributes: {len(all_data_attrs)}")
        if api_endpoints:
            print(f"  - API endpoints found: {len(api_endpoints)}")
    
    def get_listing_links(self, max_pages: int = 10) -> List[str]:
        """
        Extract all listing URLs using Poshmark's API endpoint.
        
        Args:
            max_pages: Maximum number of pages to fetch (default: 10, but will fetch all if possible)
        
        Returns:
            List of listing URLs (full URLs)
        """
        import base64
        import urllib.parse
        
        listing_links = []
        seen_ids = set()
        max_id = None
        page_group_id = None
        
        # First, fetch the initial page to get page_group_id
        response = self.session.get(self.base_url, timeout=10)
        if not response.ok:
            return []
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Extract page_group_id from the page (usually in data attributes or scripts)
        scripts = soup.find_all('script')
        for script in scripts:
            if script.string and 'page_group_id' in script.string:
                # Look for page_group_id in various formats
                pg_match = re.search(r'["\']page_group_id["\']\s*:\s*["\']([^"\']+)["\']', script.string)
                if pg_match:
                    page_group_id = pg_match.group(1)
                    break
        
        # Make initial API call to get page_group_id and first page of listings
        initial_request = {
            "filters": {
                "department": "All",
                "inventory_status": ["available"]
            },
            "experience": "all",
            "count": 48,
            "static_facets": False
        }
        
        request_param = urllib.parse.quote(json.dumps(initial_request))
        api_url = f"https://poshmark.com/vm-rest/users/{self.closet_username}/posts/filtered?request={request_param}&summarize=true&app_version=2.55&pm_version=2025.45.0"
        
        initial_data = None
        try:
            print(f"Making initial API call to: {api_url[:100]}...")
            api_response = self.session.get(api_url, timeout=10)
            if api_response.ok:
                initial_data = api_response.json()
                print(f"Initial API call successful, response type: {type(initial_data)}")
                # Extract page_group_id from response if available
                if isinstance(initial_data, dict) and not page_group_id:
                    # Look for page_group_id in the response
                    def find_page_group_id(obj):
                        if isinstance(obj, dict):
                            if 'page_group_id' in obj:
                                return obj['page_group_id']
                            for value in obj.values():
                                result = find_page_group_id(value)
                                if result:
                                    return result
                        elif isinstance(obj, list):
                            for item in obj:
                                result = find_page_group_id(item)
                                if result:
                                    return result
                        return None
                    
                    page_group_id = find_page_group_id(initial_data)
                    
                    # Also extract listings from initial response
                    def extract_listings(obj, path="", depth=0):
                        if depth > 10:  # Prevent infinite recursion
                            return []
                        listings = []
                        if isinstance(obj, dict):
                            # Look for posts/listings array
                            if 'posts' in obj and isinstance(obj['posts'], list):
                                return obj['posts']
                            if 'data' in obj and isinstance(obj['data'], list):
                                return obj['data']
                            if 'listings' in obj and isinstance(obj['listings'], list):
                                return obj['listings']
                            # Check if this dict itself is a listing
                            if any(key in obj for key in ['canonical_path', 'path', 'id', 'title', 'listing_id', 'post_id']):
                                return [obj]
                            # Recursively search
                            for key, value in obj.items():
                                if key not in ['metadata', 'facets', 'summaries']:  # Skip non-listing data
                                    listings.extend(extract_listings(value, f"{path}.{key}", depth+1))
                        elif isinstance(obj, list):
                            # Check if this list contains listing objects
                            if obj and isinstance(obj[0], dict):
                                # Check if it looks like a listing
                                if any(key in obj[0] for key in ['canonical_path', 'path', 'id', 'title', 'listing_id', 'post_id']):
                                    return obj
                            # Otherwise recurse
                            for item in obj:
                                listings.extend(extract_listings(item, f"{path}[]", depth+1))
                        return listings
                    
                    initial_listings = extract_listings(initial_data)
                    print(f"Extracted {len(initial_listings)} listings from initial API response")
                    if initial_listings:
                        # Process initial listings
                        for listing in initial_listings:
                            if isinstance(listing, dict):
                                listing_id = listing.get('id') or listing.get('listing_id') or listing.get('post_id')
                                if listing_id and listing_id not in seen_ids:
                                    seen_ids.add(listing_id)
                                
                                url_path = None
                                if 'canonical_path' in listing:
                                    url_path = listing['canonical_path']
                                elif 'path' in listing:
                                    url_path = listing['path']
                                elif 'url' in listing:
                                    url_path = listing['url']
                                elif 'title' in listing and listing_id:
                                    title = listing['title']
                                    title_slug = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '-')
                                    url_path = f"/listing/{title_slug}-{listing_id}"
                                elif listing_id:
                                    url_path = f"/listing/{listing_id}"
                                
                                if url_path:
                                    if not url_path.startswith('http'):
                                        url_path = f"https://poshmark.com{url_path}"
                                    if url_path not in listing_links:
                                        listing_links.append(url_path)
                        
                        if listing_links:
                            print(f"Found {len(listing_links)} listings from initial API response")
                    
                    # Extract next_max_id from initial response for pagination
                    if isinstance(initial_data, dict):
                        max_id = initial_data.get('next_max_id') or initial_data.get('max_id')
                        if max_id:
                            print(f"Found next_max_id for pagination: {max_id[:50]}...")
        except Exception as e:
            print(f"Error in initial API call: {e}")
        
        # Build the API request
        # Start from page 2 if we already got listings from initial call
        page_num = 2 if listing_links else 1
        consecutive_empty = 0
        
        while page_num <= max_pages:
            # Build request payload
            request_data = {
                "filters": {
                    "department": "All",
                    "inventory_status": ["available"]
                },
                "experience": "all",
                "count": 48,
                "static_facets": False
            }
            
            # Use next_max_id from previous response if available, otherwise try page_group_id encoding
            if max_id:
                request_data["max_id"] = max_id
            elif page_group_id and page_num > 1:
                # Encode max_id with page info (fallback if next_max_id not available)
                max_id_data = {
                    "max_ids": [48 * (page_num - 1)],
                    "page_num": page_num,
                    "page_group_id": page_group_id
                }
                max_id_encoded = base64.b64encode(json.dumps(max_id_data).encode()).decode()
                # Remove padding
                max_id_encoded = max_id_encoded.rstrip('=')
                request_data["max_id"] = f"ENC_{max_id_encoded}"
            
            # Make API request
            request_param = urllib.parse.quote(json.dumps(request_data))
            api_url = f"https://poshmark.com/vm-rest/users/{self.closet_username}/posts/filtered?request={request_param}&summarize=true&app_version=2.55&pm_version=2025.45.0"
            
            try:
                print(f"Fetching page {page_num}...")
                api_response = self.session.get(api_url, timeout=10)
                
                if not api_response.ok:
                    print(f"API request failed with status {api_response.status_code}")
                    break
                
                data = api_response.json()
                
                # Extract listings from response
                def extract_listings(obj, path="", depth=0):
                    if depth > 10:  # Prevent infinite recursion
                        return []
                    listings = []
                    if isinstance(obj, dict):
                        # Look for posts/listings array
                        if 'posts' in obj and isinstance(obj['posts'], list):
                            return obj['posts']
                        if 'data' in obj and isinstance(obj['data'], list):
                            return obj['data']
                        if 'listings' in obj and isinstance(obj['listings'], list):
                            return obj['listings']
                        # Check if this dict itself is a listing
                        if any(key in obj for key in ['canonical_path', 'path', 'id', 'title', 'listing_id', 'post_id']):
                            return [obj]
                        # Recursively search
                        for key, value in obj.items():
                            if key not in ['metadata', 'facets', 'summaries']:  # Skip non-listing data
                                listings.extend(extract_listings(value, f"{path}.{key}", depth+1))
                    elif isinstance(obj, list):
                        # Check if this list contains listing objects
                        if obj and isinstance(obj[0], dict):
                            # Check if it looks like a listing
                            if any(key in obj[0] for key in ['canonical_path', 'path', 'id', 'title', 'listing_id', 'post_id']):
                                return obj
                        # Otherwise recurse
                        for item in obj:
                            listings.extend(extract_listings(item, f"{path}[]", depth+1))
                    return listings
                
                page_listings = extract_listings(data)
                
                if not page_listings:
                    consecutive_empty += 1
                    if consecutive_empty >= 2:
                        print(f"No listings found in page {page_num}, stopping.")
                        break
                    page_num += 1
                    continue
                
                consecutive_empty = 0
                
                # Extract URLs from listings
                new_count = 0
                for listing in page_listings:
                    if isinstance(listing, dict):
                        # Try different possible fields for the URL
                        listing_id = listing.get('id') or listing.get('listing_id')
                        if listing_id and listing_id not in seen_ids:
                            seen_ids.add(listing_id)
                        
                        # Get the URL/path - try multiple possible fields
                        url_path = None
                        listing_id = listing.get('id') or listing.get('listing_id') or listing.get('post_id')
                        
                        # Try different path fields
                        if 'canonical_path' in listing:
                            url_path = listing['canonical_path']
                        elif 'path' in listing:
                            url_path = listing['path']
                        elif 'url' in listing:
                            url_path = listing['url']
                        elif 'title' in listing and listing_id:
                            # Construct URL from title and ID
                            title = listing['title']
                            # Create URL-friendly slug from title
                            title_slug = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '-')
                            url_path = f"/listing/{title_slug}-{listing_id}"
                        elif listing_id:
                            # Last resort: just use ID (might not work but worth trying)
                            url_path = f"/listing/{listing_id}"
                        
                        if url_path:
                            if not url_path.startswith('http'):
                                url_path = f"https://poshmark.com{url_path}"
                            if url_path not in listing_links:
                                listing_links.append(url_path)
                                new_count += 1
                
                print(f"Found {new_count} new listings on page {page_num} (total: {len(listing_links)})")
                
                # Check if there are more pages
                # Look for next max_id in response (search recursively)
                def find_next_max_id(obj):
                    if isinstance(obj, dict):
                        if 'next_max_id' in obj:
                            return obj['next_max_id']
                        if 'max_id' in obj and obj['max_id'] != max_id:
                            return obj['max_id']
                        for value in obj.values():
                            result = find_next_max_id(value)
                            if result:
                                return result
                    elif isinstance(obj, list):
                        for item in obj:
                            result = find_next_max_id(item)
                            if result:
                                return result
                    return None
                
                next_max_id = find_next_max_id(data) if isinstance(data, dict) else None
                if next_max_id and next_max_id != max_id:
                    max_id = next_max_id
                    print(f"Using next_max_id for page {page_num + 1}")
                else:
                    # Check if we got fewer listings than requested (end of list)
                    if new_count < 48:
                        print("Reached end of listings (got fewer than requested).")
                        break
                    # If no next_max_id and we got full page, try to continue with page_group_id encoding
                    if not next_max_id and not page_group_id:
                        print("Warning: No next_max_id found and no page_group_id, pagination may be limited")
                
                # Debug: save first page response for inspection (after processing, so it doesn't block)
                if page_num == 1 and not os.getenv('AWS_LAMBDA_FUNCTION_NAME'):
                    try:
                        debug_file = 'api_response_debug.json'
                        with open(debug_file, 'w', encoding='utf-8') as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)
                        print(f"Saved first API response to {debug_file} for inspection")
                    except (OSError, IOError):
                        try:
                            debug_file = '/tmp/api_response_debug.json'
                            with open(debug_file, 'w', encoding='utf-8') as f:
                                json.dump(data, f, indent=2, ensure_ascii=False)
                            print(f"Saved first API response to {debug_file} for inspection")
                        except Exception:
                            pass  # Silently skip if we can't write debug file
                
                page_num += 1
                time.sleep(1)  # Be respectful
                
            except Exception as e:
                print(f"Error fetching page {page_num}: {e}")
                # If we got data but failed later, try to continue if possible
                error_msg = str(e)
                if 'Read-only file system' in error_msg or 'Permission denied' in error_msg:
                    # File system errors are non-critical - listings were already processed
                    print(f"Warning: Non-critical file system error, continuing...")
                    page_num += 1
                    time.sleep(1)
                    continue
                break
        
        # If API didn't work well, fall back to HTML extraction
        if len(listing_links) < 50:
            print("API extraction didn't get many results, trying HTML fallback...")
            html_text = response.text
            all_listing_urls = re.findall(r'/listing/[^"\'<>\s]+', html_text)
            for url in all_listing_urls:
                full_url = f"https://poshmark.com{url}" if not url.startswith('http') else url
                if full_url not in listing_links:
                    listing_links.append(full_url)
            
            # If we still don't have max_id or page_group_id, try to extract from HTML or make another API call
            if not max_id and not page_group_id and len(listing_links) > 0:
                print("Attempting to get pagination tokens for continued fetching...")
                # Try to extract page_group_id from HTML if we haven't found it yet
                if not page_group_id:
                    pg_match = re.search(r'["\']page_group_id["\']\s*:\s*["\']([^"\']+)["\']', html_text)
                    if pg_match:
                        page_group_id = pg_match.group(1)
                        print(f"Found page_group_id from HTML: {page_group_id}")
                
                # Try making an API call to get max_id and continue pagination
                if not max_id:
                    try:
                        initial_request = {
                            "filters": {
                                "department": "All",
                                "inventory_status": ["available"]
                            },
                            "experience": "all",
                            "count": 48,
                            "static_facets": False
                        }
                        request_param = urllib.parse.quote(json.dumps(initial_request))
                        api_url_retry = f"https://poshmark.com/vm-rest/users/{self.closet_username}/posts/filtered?request={request_param}&summarize=true&app_version=2.55&pm_version=2025.45.0"
                        api_response = self.session.get(api_url_retry, timeout=10)
                        if api_response.ok:
                            data = api_response.json()
                            def find_next_max_id(obj):
                                if isinstance(obj, dict):
                                    if 'next_max_id' in obj:
                                        return obj['next_max_id']
                                    if 'max_id' in obj:
                                        return obj['max_id']
                                    for value in obj.values():
                                        result = find_next_max_id(value)
                                        if result:
                                            return result
                                elif isinstance(obj, list):
                                    for item in obj:
                                        result = find_next_max_id(item)
                                        if result:
                                            return result
                                return None
                            max_id = find_next_max_id(data)
                            if max_id:
                                print(f"Found max_id from API call: {max_id[:50]}...")
                    except Exception as e:
                        print(f"Could not get max_id from API: {e}")
                
                # If we now have max_id or page_group_id, restart pagination from page 2
                if (max_id or page_group_id) and page_num <= max_pages:
                    print(f"Restarting pagination with tokens (max_id: {bool(max_id)}, page_group_id: {bool(page_group_id)})")
                    # Reset page_num to 2 since we already have the first page from HTML
                    page_num = 2
                    consecutive_empty = 0
                    # Re-enter the pagination loop
                    while page_num <= max_pages:
                        # Build request payload
                        request_data = {
                            "filters": {
                                "department": "All",
                                "inventory_status": ["available"]
                            },
                            "experience": "all",
                            "count": 48,
                            "static_facets": False
                        }
                        
                        # Use next_max_id from previous response if available, otherwise try page_group_id encoding
                        if max_id:
                            request_data["max_id"] = max_id
                        elif page_group_id and page_num > 1:
                            max_id_data = {
                                "max_ids": [48 * (page_num - 1)],
                                "page_num": page_num,
                                "page_group_id": page_group_id
                            }
                            max_id_encoded = base64.b64encode(json.dumps(max_id_data).encode()).decode()
                            max_id_encoded = max_id_encoded.rstrip('=')
                            request_data["max_id"] = f"ENC_{max_id_encoded}"
                        
                        # Make API request
                        request_param = urllib.parse.quote(json.dumps(request_data))
                        api_url_loop = f"https://poshmark.com/vm-rest/users/{self.closet_username}/posts/filtered?request={request_param}&summarize=true&app_version=2.55&pm_version=2025.45.0"
                        
                        try:
                            print(f"Fetching page {page_num} (restarted pagination)...")
                            api_response = self.session.get(api_url_loop, timeout=10)
                            
                            if not api_response.ok:
                                print(f"API request failed with status {api_response.status_code}")
                                break
                            
                            data = api_response.json()
                            
                            # Extract listings (reuse the same function)
                            def extract_listings(obj, path="", depth=0):
                                if depth > 10:
                                    return []
                                listings = []
                                if isinstance(obj, dict):
                                    if 'posts' in obj and isinstance(obj['posts'], list):
                                        return obj['posts']
                                    if 'data' in obj and isinstance(obj['data'], list):
                                        return obj['data']
                                    if 'listings' in obj and isinstance(obj['listings'], list):
                                        return obj['listings']
                                    if any(key in obj for key in ['canonical_path', 'path', 'id', 'title', 'listing_id', 'post_id']):
                                        return [obj]
                                    for key, value in obj.items():
                                        if key not in ['metadata', 'facets', 'summaries']:
                                            listings.extend(extract_listings(value, f"{path}.{key}", depth+1))
                                elif isinstance(obj, list):
                                    if obj and isinstance(obj[0], dict):
                                        if any(key in obj[0] for key in ['canonical_path', 'path', 'id', 'title', 'listing_id', 'post_id']):
                                            return obj
                                    for item in obj:
                                        listings.extend(extract_listings(item, f"{path}[]", depth+1))
                                return listings
                            
                            page_listings = extract_listings(data)
                            
                            if not page_listings:
                                consecutive_empty += 1
                                if consecutive_empty >= 2:
                                    print(f"No listings found in page {page_num}, stopping.")
                                    break
                                page_num += 1
                                continue
                            
                            consecutive_empty = 0
                            
                            # Extract URLs from listings
                            new_count = 0
                            for listing in page_listings:
                                if isinstance(listing, dict):
                                    listing_id = listing.get('id') or listing.get('listing_id') or listing.get('post_id')
                                    if listing_id and listing_id not in seen_ids:
                                        seen_ids.add(listing_id)
                                    
                                    url_path = None
                                    if 'canonical_path' in listing:
                                        url_path = listing['canonical_path']
                                    elif 'path' in listing:
                                        url_path = listing['path']
                                    elif 'url' in listing:
                                        url_path = listing['url']
                                    elif 'title' in listing and listing_id:
                                        title = listing['title']
                                        title_slug = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '-')
                                        url_path = f"/listing/{title_slug}-{listing_id}"
                                    elif listing_id:
                                        url_path = f"/listing/{listing_id}"
                                    
                                    if url_path:
                                        if not url_path.startswith('http'):
                                            url_path = f"https://poshmark.com{url_path}"
                                        if url_path not in listing_links:
                                            listing_links.append(url_path)
                                            new_count += 1
                            
                            print(f"Found {new_count} new listings on page {page_num} (total: {len(listing_links)})")
                            
                            # Check for next_max_id
                            def find_next_max_id(obj):
                                if isinstance(obj, dict):
                                    if 'next_max_id' in obj:
                                        return obj['next_max_id']
                                    if 'max_id' in obj and obj['max_id'] != max_id:
                                        return obj['max_id']
                                    for value in obj.values():
                                        result = find_next_max_id(value)
                                        if result:
                                            return result
                                elif isinstance(obj, list):
                                    for item in obj:
                                        result = find_next_max_id(item)
                                        if result:
                                            return result
                                return None
                            
                            next_max_id = find_next_max_id(data) if isinstance(data, dict) else None
                            if next_max_id and next_max_id != max_id:
                                max_id = next_max_id
                                print(f"Using next_max_id for page {page_num + 1}")
                            else:
                                if new_count < 48:
                                    print("Reached end of listings (got fewer than requested).")
                                    break
                            
                            page_num += 1
                            time.sleep(1)
                            
                        except Exception as e:
                            print(f"Error fetching page {page_num}: {e}")
                            break
        
        print(f"Total listings found: {len(listing_links)}")
        return listing_links
    
    def scrape_listing_details(self, listing_url: str) -> Dict:
        """
        Scrape name and description from an individual listing page.
        
        Args:
            listing_url: Full URL to the listing page
            
        Returns:
            Dictionary with name and description
        """
        soup = self.fetch_page(listing_url)
        if not soup:
            return {}
        
        details = {
            'url': listing_url,
            'name': '',
            'description': ''
        }
        
        # Extract name from h1 with class "listing__title-container"
        title_elem = soup.find('h1', class_=re.compile(r'listing__title-container', re.I))
        if title_elem:
            details['name'] = html.unescape(title_elem.get_text(strip=True))
        
        # Extract description from div with class "listing__description"
        desc_elem = soup.find('div', class_=re.compile(r'listing__description', re.I))
        if desc_elem:
            details['description'] = desc_elem.get_text(strip=False)  # Keep line breaks
        
        return details
    
    def scrape_listings(self, visit_details: bool = True, delay: float = 2.0, max_pages: int = 200) -> List[Dict]:
        """
        Scrape all listings from the closet page and optionally visit each listing for details.
        
        Args:
            visit_details: If True, visit each listing page for detailed info (default: True)
            delay: Delay between requests in seconds (default: 2.0)
            max_pages: Maximum number of pages to fetch (default: 10)
        
        Returns:
            List of dictionaries containing listing information
        """
        print("Finding listing links...")
        listing_urls = self.get_listing_links(max_pages=max_pages)
        
        if not listing_urls:
            print("No listing links found.")
            return []
        
        print(f"Found {len(listing_urls)} listings")
        
        listings = []
        
        for i, listing_url in enumerate(listing_urls, 1):
            print(f"Processing listing {i}/{len(listing_urls)}: {listing_url}")
            
            if visit_details:
                # Visit the individual listing page for detailed info
                listing_info = self.scrape_listing_details(listing_url)
                if listing_info:
                    listings.append(listing_info)
            else:
                # Just get basic info from closet page
                soup = self.fetch_page(listing_url)
                if soup:
                    listing_info = self.extract_listing_info(soup)
                    listing_info['link'] = listing_url
                    if listing_info['title']:
                        listings.append(listing_info)
            
            # Be respectful with delays
            if i < len(listing_urls):
                time.sleep(delay)
        
        return listings
    
    def generate_slug(self, listing_url: str) -> str:
        """
        Generate a slug from a listing URL.
        
        Args:
            listing_url: Full listing URL
            
        Returns:
            Slug string (filename-safe)
        """
        # Extract the listing ID from URL (last part after last dash)
        # e.g., /listing/Title-Here-68f06282f59cc4ded765a577 -> 68f06282f59cc4ded765a577
        match = re.search(r'-([a-f0-9]+)$', listing_url)
        if match:
            return match.group(1)
        
        # Fallback: use the entire path, sanitized
        parsed = urlparse(listing_url)
        path = parsed.path.strip('/')
        # Replace slashes and special chars
        slug = re.sub(r'[^a-zA-Z0-9_-]', '_', path)
        return slug[:100]  # Limit length
    
    def save_listing_to_s3(self, listing: Dict) -> bool:
        """
        Save a single listing to S3 using its slug as the key.
        
        Args:
            listing: Listing dictionary
            
        Returns:
            True if successful, False otherwise
        """
        if not self.s3_bucket or not self.s3_client:
            return False
        
        if 'url' not in listing:
            return False
        
        slug = self.generate_slug(listing['url'])
        key = f"{self.s3_prefix}/{slug}.json"
        
        try:
            self.s3_client.put_object(
                Bucket=self.s3_bucket,
                Key=key,
                Body=json.dumps(listing, indent=2, ensure_ascii=False).encode('utf-8'),
                ContentType='application/json'
            )
            return True
        except Exception as e:
            print(f"Error saving {slug} to S3: {e}")
            return False
    
    def get_existing_slugs_from_s3(self) -> set:
        """
        Get all existing listing slugs from S3.
        
        Returns:
            Set of slugs (without .json extension)
        """
        if not self.s3_bucket or not self.s3_client:
            return set()
        
        slugs = set()
        try:
            paginator = self.s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(
                Bucket=self.s3_bucket,
                Prefix=f"{self.s3_prefix}/"
            )
            
            for page in pages:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        key = obj['Key']
                        # Extract slug from key (remove prefix and .json)
                        if key.endswith('.json'):
                            slug = key.replace(f"{self.s3_prefix}/", "").replace(".json", "")
                            slugs.add(slug)
        except Exception as e:
            print(f"Error reading slugs from S3: {e}")
        
        return slugs
    
    def generate_index_html(self, stats: Dict, total_listings: int, last_backup_time: str) -> str:
        """
        Generate an HTML index page with backup statistics.
        
        Args:
            stats: Dictionary with stats from scraping
            total_listings: Total number of listings in S3
            last_backup_time: Timestamp of last backup
            
        Returns:
            HTML string
        """
        html_template = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Poshmark Backup Status - {closet_username}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .container {{
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            padding: 40px;
            max-width: 600px;
            width: 100%;
        }}
        h1 {{
            color: #333;
            margin-bottom: 10px;
            font-size: 2em;
        }}
        .subtitle {{
            color: #666;
            margin-bottom: 30px;
            font-size: 1.1em;
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        .stat-card {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 25px;
            border-radius: 15px;
            text-align: center;
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
        }}
        .stat-value {{
            font-size: 2.5em;
            font-weight: bold;
            margin-bottom: 5px;
        }}
        .stat-label {{
            font-size: 0.9em;
            opacity: 0.9;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        .last-backup {{
            background: #f8f9fa;
            padding: 20px;
            border-radius: 10px;
            margin-top: 20px;
            text-align: center;
        }}
        .last-backup-label {{
            color: #666;
            font-size: 0.9em;
            margin-bottom: 5px;
        }}
        .last-backup-time {{
            color: #333;
            font-size: 1.3em;
            font-weight: 600;
        }}
        .footer {{
            margin-top: 30px;
            text-align: center;
            color: #999;
            font-size: 0.85em;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📦 Poshmark Backup</h1>
        <p class="subtitle">Closet: <strong>{closet_username}</strong></p>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">{total_listings}</div>
                <div class="stat-label">Total Listings</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{backed_up}</div>
                <div class="stat-label">Backed Up</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{new_listings}</div>
                <div class="stat-label">New Today</div>
            </div>
        </div>
        
        <div class="last-backup">
            <div class="last-backup-label">Last Backup</div>
            <div class="last-backup-time">{last_backup_time}</div>
        </div>
        
        <div class="footer">
            <p>Backup status page • Updated automatically</p>
        </div>
    </div>
</body>
</html>"""
        
        return html_template.format(
            closet_username=self.closet_username,
            total_listings=total_listings,
            backed_up=stats.get('total', 0),
            new_listings=stats.get('new', 0),
            last_backup_time=last_backup_time
        )
    
    def upload_index_to_s3(self, stats: Dict, total_listings: int, last_backup_time: str) -> bool:
        """
        Upload the index.html page to S3 with public read access.
        
        Args:
            stats: Dictionary with stats from scraping
            total_listings: Total number of listings in S3
            last_backup_time: Timestamp of last backup
            
        Returns:
            True if successful, False otherwise
        """
        if not self.s3_bucket or not self.s3_client:
            return False
        
        try:
            html_content = self.generate_index_html(stats, total_listings, last_backup_time)
            key = f"{self.s3_prefix}/index.html"
            
            self.s3_client.put_object(
                Bucket=self.s3_bucket,
                Key=key,
                Body=html_content.encode('utf-8'),
                ContentType='text/html',
                ACL='public-read'  # Make index.html publicly readable
            )
            
            print(f"✅ Index page uploaded to s3://{self.s3_bucket}/{key}")
            return True
        except Exception as e:
            print(f"Error uploading index page to S3: {e}")
            return False
    
    def scrape_listings_with_s3(self, visit_details: bool = True, delay: float = 2.0, 
                                 max_pages: int = 200, incremental: bool = True) -> Dict:
        """
        Scrape listings and save to S3, with incremental update support.
        
        Args:
            visit_details: If True, visit each listing page for detailed info
            delay: Delay between requests in seconds
            max_pages: Maximum number of pages to fetch
            incremental: If True, only process new listings (compare with existing S3 slugs)
        
        Returns:
            Dictionary with stats: {'total': int, 'new': int, 'updated': int, 'skipped': int}
        """
        if not self.s3_bucket:
            raise ValueError("S3 bucket not configured")
        
        # Get existing slugs if incremental
        existing_slugs = set()
        if incremental:
            print("Fetching existing listings from S3...")
            existing_slugs = self.get_existing_slugs_from_s3()
            print(f"Found {len(existing_slugs)} existing listings in S3")
        
        # Get all listing URLs
        print("Finding listing links...")
        listing_urls = self.get_listing_links(max_pages=max_pages)
        
        if not listing_urls:
            print("No listing links found.")
            return {'total': 0, 'new': 0, 'updated': 0, 'skipped': 0}
        
        print(f"Found {len(listing_urls)} listings")
        
        stats = {'total': 0, 'new': 0, 'updated': 0, 'skipped': 0}
        
        for i, listing_url in enumerate(listing_urls, 1):
            slug = self.generate_slug(listing_url)
            
            # Skip if already exists and incremental mode
            if incremental and slug in existing_slugs:
                stats['skipped'] += 1
                if i % 50 == 0:
                    print(f"Processed {i}/{len(listing_urls)} (skipped {stats['skipped']} existing)")
                continue
            
            print(f"Processing listing {i}/{len(listing_urls)}: {listing_url}")
            
            if visit_details:
                listing_info = self.scrape_listing_details(listing_url)
                if listing_info:
                    listing_info['url'] = listing_url  # Ensure URL is set
                    listing_info['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
                    
                    if self.save_listing_to_s3(listing_info):
                        if slug in existing_slugs:
                            stats['updated'] += 1
                        else:
                            stats['new'] += 1
                        stats['total'] += 1
            else:
                soup = self.fetch_page(listing_url)
                if soup:
                    listing_info = self.extract_listing_info(soup)
                    listing_info['url'] = listing_url
                    listing_info['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
                    
                    if self.save_listing_to_s3(listing_info):
                        if slug in existing_slugs:
                            stats['updated'] += 1
                        else:
                            stats['new'] += 1
                        stats['total'] += 1
            
            # Be respectful with delays
            if i < len(listing_urls):
                time.sleep(delay)
        
        # Get total listings count after scraping
        print("\nGetting final listing count...")
        final_slugs = self.get_existing_slugs_from_s3()
        total_listings = len(final_slugs)
        
        # Generate and upload index page
        last_backup_time = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
        print("\nGenerating index page...")
        self.upload_index_to_s3(stats, total_listings, last_backup_time)
        
        return stats
    
    def save_to_json(self, listings: List[Dict], filename: str = None):
        """
        Save listings to a JSON file.
        
        Args:
            listings: List of listing dictionaries
            filename: Output filename (defaults to {username}_listings.json)
        """
        if filename is None:
            filename = f"{self.closet_username}_listings.json"
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(listings, f, indent=2, ensure_ascii=False)
        
        print(f"Saved {len(listings)} listings to {filename}")
    
    def save_to_csv(self, listings: List[Dict], filename: str = None):
        """
        Save listings to a CSV file.
        
        Args:
            listings: List of listing dictionaries
            filename: Output filename (defaults to {username}_listings.csv)
        """
        import csv
        
        if filename is None:
            filename = f"{self.closet_username}_listings.csv"
        
        if not listings:
            print("No listings to save.")
            return
        
        fieldnames = listings[0].keys()
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(listings)
        
        print(f"Saved {len(listings)} listings to {filename}")


def main():
    """Main function to run the scraper."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Scrape Poshmark closet listings')
    parser.add_argument('username', nargs='?', default='emily2636',
                       help='Poshmark closet username (default: emily2636)')
    parser.add_argument('--format', choices=['json', 'csv', 'both'], default='json',
                       help='Output format (default: json)')
    parser.add_argument('--output', '-o', help='Output filename (without extension)')
    parser.add_argument('--no-details', dest='visit_details', action='store_false',
                       default=True, help='Skip visiting individual listing pages')
    parser.add_argument('--delay', type=float, default=2.0,
                       help='Delay between requests in seconds (default: 2.0)')
    parser.add_argument('--max-pages', type=int, default=200,
                       help='Maximum number of pages to fetch (default: 200, enough for ~10k listings)')
    parser.add_argument('--debug', action='store_true',
                       help='Run debug mode to analyze page structure')
    parser.add_argument('--s3-bucket', help='S3 bucket name for storing listings')
    parser.add_argument('--s3-prefix', help='S3 prefix/path for storing listings (default: username)')
    parser.add_argument('--s3-only', action='store_true',
                       help='Only save to S3, skip local file output')
    parser.add_argument('--incremental', action='store_true', default=True,
                       help='Only process new listings (compare with existing S3 slugs) (default: True)')
    parser.add_argument('--no-incremental', dest='incremental', action='store_false',
                       help='Process all listings, even if they exist in S3')
    
    args = parser.parse_args()
    
    scraper = PoshmarkScraper(args.username, s3_bucket=args.s3_bucket, s3_prefix=args.s3_prefix)
    
    # Run debug mode if requested
    if args.debug:
        scraper.debug_page_structure()
        return
    
    print(f"Scraping Poshmark closet: {args.username}")
    print(f"URL: https://poshmark.com/closet/{args.username}?availability=available")
    print("-" * 50)
    
    # Use S3 if configured
    if scraper.s3_bucket:
        print("Using S3 storage mode")
        stats = scraper.scrape_listings_with_s3(
            visit_details=args.visit_details,
            delay=args.delay,
            max_pages=args.max_pages,
            incremental=args.incremental
        )
        
        print("\n" + "=" * 50)
        print("Scraping Summary:")
        print(f"  Total processed: {stats['total']}")
        print(f"  New listings: {stats['new']}")
        print(f"  Updated listings: {stats['updated']}")
        print(f"  Skipped (already exist): {stats['skipped']}")
        print("=" * 50)
        
        # Also save locally if not s3-only
        if not args.s3_only:
            print("\nFetching all listings from S3 for local save...")
            # For local save, we'd need to fetch from S3 or re-scrape
            # For now, just note that listings are in S3
            print("Listings saved to S3. Use --s3-only to skip local file output.")
    else:
        # Traditional local file mode
        listings = scraper.scrape_listings(visit_details=args.visit_details, delay=args.delay, max_pages=args.max_pages)
        
        if not listings:
            print("No listings found.")
            return
        
        print(f"\nFound {len(listings)} listings")
        
        # Display first few listings as preview
        for i, listing in enumerate(listings[:3], 1):
            print(f"\nListing {i}:")
            if 'name' in listing:
                print(f"  Name: {listing['name']}")
            elif 'title' in listing:
                print(f"  Title: {listing['title']}")
            if 'description' in listing and listing['description']:
                desc_preview = listing['description'][:100] + '...' if len(listing['description']) > 100 else listing['description']
                print(f"  Description: {desc_preview}")
        
        # Save to file(s)
        if args.format in ['json', 'both']:
            filename = f"{args.output}.json" if args.output else None
            scraper.save_to_json(listings, filename)
        
        if args.format in ['csv', 'both']:
            filename = f"{args.output}.csv" if args.output else None
            scraper.save_to_csv(listings, filename)


def lambda_handler(event, context):
    """
    AWS Lambda handler function.
    
    Expected event structure:
    {
        "username": "emily2636",
        "s3_bucket": "my-bucket",
        "s3_prefix": "poshmark/emily2636",  # optional
        "incremental": true,  # optional, default true
        "delay": 1.0,  # optional
        "max_pages": 200  # optional
    }
    """
    username = event.get('username', 'emily2636')
    s3_bucket = event.get('s3_bucket') or os.getenv('S3_BUCKET')
    s3_prefix = event.get('s3_prefix') or username
    incremental = event.get('incremental', True)
    delay = event.get('delay', 1.0)
    max_pages = event.get('max_pages', 200)
    
    if not s3_bucket:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'S3 bucket not specified'})
        }
    
    try:
        scraper = PoshmarkScraper(username, s3_bucket=s3_bucket, s3_prefix=s3_prefix)
        
        stats = scraper.scrape_listings_with_s3(
            visit_details=True,
            delay=delay,
            max_pages=max_pages,
            incremental=incremental
        )
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'success': True,
                'stats': stats
            })
        }
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps({
                'success': False,
                'error': str(e)
            })
        }


if __name__ == '__main__':
    main()

