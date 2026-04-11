"""
AUTONOMOUS WEB NAVIGATION MODULE
=================================
Enables Jarvis to navigate web pages autonomously:
- Open URLs
- Extract text content
- Scroll and navigate
- Click links
- Multi-site research
- Cross-verification
"""

import logging
import time
import webbrowser
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import requests
from bs4 import BeautifulSoup
import re

logger = logging.getLogger(__name__)


@dataclass
class WebPage:
    """Represents a web page"""
    url: str
    title: str
    content: str
    links: List[str]
    timestamp: float
    
    def __str__(self):
        return f"WebPage({self.title}, {len(self.content)} chars, {len(self.links)} links)"


class AutonomousWebNavigator:
    """
    Autonomous web navigator for Jarvis
    
    Features:
    - Open and navigate to URLs
    - Extract and parse content
    - Follow links
    - Multi-site research
    - Content verification
    """
    
    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.visited_urls: List[str] = []
        self.page_cache: Dict[str, WebPage] = {}
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        logger.info("Autonomous Web Navigator initialized")
    
    def navigate_to(self, url: str, extract_content: bool = True) -> Dict[str, Any]:
        """
        Navigate to a URL and optionally extract content
        
        Args:
            url: URL to navigate to
            extract_content: If True, extract and parse content
            
        Returns:
            Dict with navigation result
        """
        try:
            # Normalize URL
            if not url.startswith(('http://', 'https://')):
                url = f'https://{url}'
            
            logger.info(f"🌐 Navigating to: {url}")
            
            # Check cache
            if url in self.page_cache:
                logger.info(f"📦 Using cached version")
                page = self.page_cache[url]
                return {
                    'success': True,
                    'cached': True,
                    'title': page.title,
                    'content': page.content[:500],  # Summary
                    'links_count': len(page.links)
                }
            
            # Open in browser
            webbrowser.open(url)
            
            if not extract_content:
                self.visited_urls.append(url)
                return {
                    'success': True,
                    'url': url,
                    'message': 'Page opened in browser'
                }
            
            # Fetch and parse content
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            
            # Parse HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract title
            title = soup.title.string if soup.title else 'No title'
            
            # Extract main content
            content = self._extract_main_content(soup)
            
            # Extract links
            links = self._extract_links(soup, url)
            
            # Create page object
            page = WebPage(
                url=url,
                title=title,
                content=content,
                links=links,
                timestamp=time.time()
            )
            
            # Cache page
            self.page_cache[url] = page
            self.visited_urls.append(url)
            
            logger.info(f"✓ Page loaded: {title}")
            logger.info(f"  Content: {len(content)} chars")
            logger.info(f"  Links: {len(links)}")
            
            return {
                'success': True,
                'url': url,
                'title': title,
                'content': content[:1000],  # First 1000 chars
                'links_count': len(links),
                'cached': False
            }
        
        except requests.RequestException as e:
            logger.error(f"Navigation failed: {e}")
            # Still try to open in browser
            webbrowser.open(url)
            return {
                'success': False,
                'error': str(e),
                'message': 'Page opened in browser but content extraction failed'
            }
        
        except Exception as e:
            logger.error(f"Navigation error: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _extract_main_content(self, soup: BeautifulSoup) -> str:
        """Extract main text content from page"""
        # Remove script and style elements
        for script in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
            script.decompose()
        
        # Get text
        text = soup.get_text(separator=' ', strip=True)
        
        # Clean up whitespace
        text = re.sub(r'\s+', ' ', text)
        
        return text.strip()
    
    def _extract_links(self, soup: BeautifulSoup, base_url: str) -> List[str]:
        """Extract all links from page"""
        links = []
        
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            
            # Convert relative to absolute
            if href.startswith('/'):
                from urllib.parse import urlparse
                parsed = urlparse(base_url)
                href = f"{parsed.scheme}://{parsed.netloc}{href}"
            elif not href.startswith(('http://', 'https://')):
                continue
            
            # Filter out common non-content links
            if any(skip in href.lower() for skip in ['javascript:', 'mailto:', '#', 'login', 'signup']):
                continue
            
            links.append(href)
        
        return list(set(links))[:20]  # Max 20 unique links
    
    def search_and_navigate(self, query: str, num_results: int = 3) -> Dict[str, Any]:
        """
        Search for query and navigate to top results
        
        Args:
            query: Search query
            num_results: Number of results to visit
            
        Returns:
            Dict with aggregated results
        """
        try:
            logger.info(f"🔍 Searching for: {query}")
            
            # Use DuckDuckGo for search (privacy-friendly)
            from ddgs import DDGS
            
            results = []
            visited_pages = []
            
            with DDGS() as ddgs:
                search_results = list(ddgs.text(query, max_results=num_results))
            
            if not search_results:
                return {
                    'success': False,
                    'message': 'No search results found'
                }
            
            logger.info(f"Found {len(search_results)} results")
            
            # Navigate to each result
            for i, result in enumerate(search_results[:num_results], 1):
                url = result['href']
                title = result['title']
                snippet = result['body']
                
                logger.info(f"\n📄 Result {i}: {title}")
                logger.info(f"   URL: {url}")
                
                # Navigate and extract content
                nav_result = self.navigate_to(url, extract_content=True)
                
                if nav_result['success']:
                    visited_pages.append({
                        'url': url,
                        'title': title,
                        'snippet': snippet,
                        'content': nav_result.get('content', ''),
                        'cached': nav_result.get('cached', False)
                    })
                
                # Small delay between requests
                if i < num_results:
                    time.sleep(1)
            
            # Synthesize findings
            synthesis = self._synthesize_findings(visited_pages, query)
            
            return {
                'success': True,
                'query': query,
                'pages_visited': len(visited_pages),
                'results': visited_pages,
                'synthesis': synthesis
            }
        
        except Exception as e:
            logger.error(f"Search and navigate failed: {e}")
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'error': str(e)
            }
    
    def _synthesize_findings(self, pages: List[Dict], query: str) -> str:
        """Synthesize findings from multiple pages"""
        if not pages:
            return "No information found"
        
        # Extract key information
        all_content = []
        sources = []
        
        for page in pages:
            content = page.get('content', '')
            title = page.get('title', '')
            url = page.get('url', '')
            
            # Extract relevant sentences (containing query terms)
            query_terms = query.lower().split()
            relevant_sentences = []
            
            for sentence in content.split('.'):
                if any(term in sentence.lower() for term in query_terms):
                    relevant_sentences.append(sentence.strip())
                    if len(relevant_sentences) >= 3:
                        break
            
            if relevant_sentences:
                all_content.extend(relevant_sentences)
                sources.append(f"{title} ({url})")
        
        # Create synthesis
        synthesis = f"I checked {len(pages)} sources. "
        
        if all_content:
            # Take first 3-5 relevant sentences
            key_facts = all_content[:5]
            synthesis += ' '.join(key_facts[:3])  # Limit to 3 sentences
        else:
            synthesis += "Found general information but no specific details on the query."
        
        synthesis += f"\n\nSources: {', '.join(sources[:3])}"
        
        return synthesis
    
    def get_page_content(self, url: str) -> Optional[str]:
        """Get cached page content if available"""
        if url in self.page_cache:
            return self.page_cache[url].content
        return None
    
    def clear_cache(self):
        """Clear page cache"""
        self.page_cache.clear()
        self.visited_urls.clear()
        logger.info("Web navigation cache cleared")
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get navigation statistics"""
        return {
            'pages_visited': len(self.visited_urls),
            'pages_cached': len(self.page_cache),
            'unique_domains': len(set(self._get_domain(url) for url in self.visited_urls))
        }
    
    def _get_domain(self, url: str) -> str:
        """Extract domain from URL"""
        from urllib.parse import urlparse
        return urlparse(url).netloc


# ============================================================================
# SIMPLE WEB CONTENT FETCHER (Fallback)
# ============================================================================

class SimpleWebFetcher:
    """
    Lightweight web fetcher for quick content extraction
    No browser automation - just HTTP requests
    """
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def fetch_content(self, url: str) -> Dict[str, Any]:
        """Fetch and extract text content from URL"""
        try:
            if not url.startswith(('http://', 'https://')):
                url = f'https://{url}'
            
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Remove unwanted elements
            for tag in soup(['script', 'style', 'nav', 'header', 'footer']):
                tag.decompose()
            
            title = soup.title.string if soup.title else 'No title'
            text = soup.get_text(separator=' ', strip=True)
            text = re.sub(r'\s+', ' ', text).strip()
            
            return {
                'success': True,
                'url': url,
                'title': title,
                'content': text[:2000],  # First 2000 chars
                'length': len(text)
            }
        
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }


# ============================================================================
# EXPORT
# ============================================================================

__all__ = [
    'AutonomousWebNavigator',
    'SimpleWebFetcher',
    'WebPage'
]