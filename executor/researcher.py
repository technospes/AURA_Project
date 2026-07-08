"""
DEEP RESEARCH AGENT
====================
GAP 4: Real multi-source research instead of search → summarise.

Pipeline:
  1. PLAN     — break the query into 3–5 targeted sub-queries
  2. SEARCH   — fetch results for each sub-query in parallel
  3. EXTRACT  — pull structured content from each page
  4. RANK     — score sources by freshness, relevance, authority
  5. COMPARE  — detect consensus vs disagreement across sources
  6. SYNTHESISE — LLM summary with citations + structured output

The result is rich enough for Jarvis to both SPEAK a summary and
optionally WRITE a full report to disk.

Usage (from background task):
    researcher = DeepResearcher(groq_api_key=key)
    result = await researcher.research("best laptop under 70k INR")

Result schema:
    {
      "topic": str,
      "synthesis": str,          # Spoken summary (2-3 sentences)
      "full_report": str,        # Detailed markdown report
      "sources": [               # Ranked list
          {"title": str, "url": str, "relevance": float, "snippet": str}
      ],
      "key_facts": [str],        # Bullet points
      "confidence": float,       # 0-1 how confident the synthesis is
      "sources_count": int,
    }
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    content: str = ""
    relevance_score: float = 0.0
    authority_score: float = 0.0
    freshness_score: float = 0.0
    final_score: float = 0.0


@dataclass
class ResearchResult:
    topic: str
    synthesis: str                   # 2-3 sentence spoken summary
    full_report: str                 # Full markdown report
    sources: List[Dict]
    key_facts: List[str]
    confidence: float
    sources_count: int
    duration_seconds: float = 0.0
    error: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════
# QUERY PLANNER — Breaks one query into multiple targeted sub-queries
# ══════════════════════════════════════════════════════════════════════════

class QueryPlanner:
    """Expands a user query into 3-5 targeted sub-queries for broader coverage."""

    # Topic-specific query expansion templates
    TEMPLATES = {
        "laptop": [
            "{topic} specifications",
            "{topic} review 2024 2025",
            "{topic} vs alternatives comparison",
            "{topic} pros cons",
        ],
        "phone": [
            "{topic} camera battery performance",
            "{topic} review 2024 2025",
            "{topic} best price India",
            "{topic} vs competitors",
        ],
        "investment": [
            "{topic} analysis outlook",
            "{topic} risk return",
            "{topic} expert opinion 2025",
        ],
        "how to": [
            "{topic} step by step guide",
            "{topic} tutorial beginner",
            "{topic} common mistakes avoid",
        ],
        "_default": [
            "{topic}",
            "{topic} explained",
            "{topic} latest news 2025",
            "best {topic} review",
        ]
    }

    def plan(self, topic: str, max_queries: int = 4) -> List[str]:
        """Generate targeted sub-queries for a topic."""
        topic_lower = topic.lower()

        template_key = "_default"
        for key in self.TEMPLATES:
            if key != "_default" and key in topic_lower:
                template_key = key
                break

        templates = self.TEMPLATES[template_key][:max_queries]
        queries = [t.format(topic=topic) for t in templates]

        # Ensure the original query is always first
        if queries[0] != topic:
            queries.insert(0, topic)
            queries = queries[:max_queries]

        logger.info(f" Research plan ({len(queries)} queries): {queries}")
        return queries


# ══════════════════════════════════════════════════════════════════════════
# SOURCE RANKER — Scores results by relevance + authority + freshness
# ══════════════════════════════════════════════════════════════════════════

class SourceRanker:
    """Scores search results so the synthesiser uses the best sources first."""

    # Domain authority scores (0-1)
    AUTHORITY_DOMAINS = {
        # High authority
        "reddit.com": 0.7, "youtube.com": 0.5,
        "wikipedia.org": 0.85, "github.com": 0.8,
        "stackoverflow.com": 0.85,
        # Tech/review sites
        "rtings.com": 0.9, "notebookcheck.net": 0.9,
        "gsmarena.com": 0.9, "anandtech.com": 0.85,
        "tomshardware.com": 0.85, "theverge.com": 0.8,
        "techradar.com": 0.8, "wired.com": 0.8,
        # Finance
        "moneycontrol.com": 0.8, "economictimes.com": 0.8,
        "investopedia.com": 0.85,
        # News
        "bbc.com": 0.9, "reuters.com": 0.9, "apnews.com": 0.9,
    }

    # Penalty domains (low quality)
    PENALTY_DOMAINS = {
        "pinterest.com", "quora.com", "answers.yahoo.com",
    }

    YEAR_TOKENS = {"2024", "2025", "2026", "latest", "new", "updated"}

    def score(self, result: SearchResult, topic: str) -> SearchResult:
        """Score and annotate a search result."""
        result.relevance_score = self._relevance(result, topic)
        result.authority_score = self._authority(result.url)
        result.freshness_score = self._freshness(result.title + " " + result.snippet)
        result.final_score = (
            result.relevance_score * 0.5 +
            result.authority_score * 0.3 +
            result.freshness_score * 0.2
        )
        return result

    def rank(self, results: List[SearchResult], topic: str) -> List[SearchResult]:
        """Score and sort results, best first."""
        scored = [self.score(r, topic) for r in results]
        scored.sort(key=lambda r: r.final_score, reverse=True)
        return scored

    def _relevance(self, result: SearchResult, topic: str) -> float:
        topic_words = set(topic.lower().split())
        title_words = set(result.title.lower().split())
        snippet_words = set(result.snippet.lower().split())

        title_overlap = len(topic_words & title_words) / max(len(topic_words), 1)
        snippet_overlap = len(topic_words & snippet_words) / max(len(topic_words), 1)
        return min(title_overlap * 0.6 + snippet_overlap * 0.4, 1.0)

    def _authority(self, url: str) -> float:
        url_lower = url.lower()
        for domain, score in self.AUTHORITY_DOMAINS.items():
            if domain in url_lower:
                return score
        for domain in self.PENALTY_DOMAINS:
            if domain in url_lower:
                return 0.2
        return 0.5  # Unknown domain

    def _freshness(self, text: str) -> float:
        text_lower = text.lower()
        hits = sum(1 for token in self.YEAR_TOKENS if token in text_lower)
        return min(hits * 0.3, 1.0)


# ══════════════════════════════════════════════════════════════════════════
# DEEP RESEARCHER — Main orchestrator
# ══════════════════════════════════════════════════════════════════════════

class DeepResearcher:
    """
    Full multi-source research pipeline.

    Designed to run as a background task via BackgroundTaskManager.
    Reports progress via task_manager.update_progress().
    """

    def __init__(self, groq_api_key: str):
        self._api_key = groq_api_key
        self._client = None
        self._planner = QueryPlanner()
        self._ranker = SourceRanker()

    def _get_client(self):
        if not self._client:
            from groq import Groq
            self._client = Groq(api_key=self._api_key)
        return self._client

    async def research(
        self,
        topic: str,
        task_manager=None,
        task_id: str = "",
        max_sources: int = 8,
    ) -> ResearchResult:
        """
        Full research pipeline. Returns a ResearchResult.

        Args:
            topic:       What to research
            task_manager: Optional — for progress reporting
            task_id:     Task ID for progress updates
            max_sources: Maximum sources to collect
        """
        start = time.time()

        def progress(pct: float, msg: str):
            if task_manager and task_id:
                task_manager.update_progress(task_id, pct, msg)
            logger.info(f"  [{pct*100:.0f}%] {msg}")

        try:
            # ── STEP 1: Plan queries ──────────────────────────────────────
            progress(0.05, "Planning research approach...")
            queries = self._planner.plan(topic, max_queries=4)

            # ── STEP 2: Search in parallel ────────────────────────────────
            progress(0.15, f"Searching {len(queries)} queries in parallel...")
            all_results = await self._parallel_search(queries)

            if not all_results:
                return ResearchResult(
                    topic=topic, synthesis="I couldn't find reliable information on that, Sir.",
                    full_report="No results found.", sources=[], key_facts=[],
                    confidence=0.0, sources_count=0,
                    duration_seconds=time.time() - start,
                    error="No search results"
                )

            # ── STEP 3: Rank sources ──────────────────────────────────────
            progress(0.45, f"Ranking {len(all_results)} sources...")
            ranked = self._ranker.rank(all_results, topic)
            top = ranked[:max_sources]

            # ── STEP 4: Extract structured content ───────────────────────
            progress(0.55, "Extracting content from top sources...")
            # Content is already in snippets from search; for a real
            # implementation, fetch full pages here via web_navigation.py
            content_blocks = self._extract_content(top, topic)

            # ── STEP 5: Detect consensus/disagreement ────────────────────
            progress(0.65, "Analysing sources...")
            consensus = self._detect_consensus(content_blocks)

            # ── STEP 6: Synthesise ────────────────────────────────────────
            progress(0.80, "Synthesising findings...")
            synthesis, key_facts, full_report = await self._synthesise(
                topic, content_blocks, consensus, top
            )

            confidence = self._estimate_confidence(top, consensus)
            progress(1.0, "Done")
            try:
                from jarvis_patch.core_patch import event_bus
                event_bus.publish("RESEARCH_DONE", {
                    "summary": synthesis[:300],
                    "topic": topic,
                    "confidence": confidence,
                    "sources_count": len(top),
                    "key_facts": key_facts[:3],
                })
                logger.info(f"[Research]  Published RESEARCH_DONE event for '{topic}'")
            except ImportError:
                logger.debug("[Research] EventBus not available — skipping publication")
            except Exception as e:
                logger.warning(f"[Research] EventBus publish failed (non-fatal): {e}")

            return ResearchResult(
                topic=topic,
                synthesis=synthesis,
                full_report=full_report,
                sources=[{
                    "title": r.title,
                    "url": r.url,
                    "relevance": round(r.final_score, 2),
                    "snippet": r.snippet[:200]
                } for r in top[:5]],
                key_facts=key_facts,
                confidence=confidence,
                sources_count=len(top),
                duration_seconds=time.time() - start,
            )

        except Exception as e:
            logger.error(f"Research failed: {e}", exc_info=True)
            try:
                from jarvis_patch.core_patch import event_bus
                event_bus.publish("RESEARCH_DONE", {
                    "summary": f"I ran into a problem researching {topic}, Sir. {str(e)[:100]}",
                    "topic": topic,
                    "confidence": 0.0,
                    "sources_count": 0,
                    "key_facts": [],
                    "error": str(e)[:200],
                })
            except Exception:
                pass
            
            return ResearchResult(
                topic=topic,
                synthesis=f"I ran into a problem researching that, Sir. {str(e)[:80]}",
                full_report=f"Error: {e}",
                sources=[], key_facts=[],
                confidence=0.0, sources_count=0,
                duration_seconds=time.time() - start,
                error=str(e)
            )

    async def _parallel_search(self, queries: List[str]) -> List[SearchResult]:
        """Run multiple searches in parallel using the new ddgs package."""
        loop = asyncio.get_event_loop()

        def _one_search(query: str) -> List[SearchResult]:
            try:
                from ddgs import DDGS
                with DDGS(timeout=12) as ddgs:
                    results = list(ddgs.text(query, max_results=3))
                
                return [
                    SearchResult(
                        title=r.get("title", ""),
                        url=r.get("href", ""),
                        snippet=r.get("body", ""),
                        content=r.get("body", "") 
                    ) for r in results
                ]
            except ImportError:
                logger.warning(f"ddgs package missing. Run: pip install ddgs")
                return []
            except Exception as e:
                logger.warning(f"DDGS search failed for '{query}': {e}")
                return []

        tasks = [loop.run_in_executor(None, _one_search, q) for q in queries]
        try:
            results_lists = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            logger.warning(f"Research search timed out after 30s")
            results_lists = []

        # Flatten + deduplicate by URL
        seen_urls: set = set()
        all_results: List[SearchResult] = []
        for batch in results_lists:
            if isinstance(batch, Exception) or not batch:
                continue
            for r in batch:
                if r.url and r.url not in seen_urls:
                    seen_urls.add(r.url)
                    all_results.append(r)

        logger.info(f" Collected {len(all_results)} unique sources")
        return all_results

    def _extract_content(
        self, results: List[SearchResult], topic: str
    ) -> List[Dict]:
        """Extract structured content blocks from results."""
        blocks = []
        for r in results:
            # Use full content if available, fall back to snippet
            text = r.content if r.content else r.snippet
            if len(text) > 100:
                blocks.append({
                    "source": r.title,
                    "url": r.url,
                    "text": text[:800],
                    "score": r.final_score,
                })
        return blocks

    def _detect_consensus(self, blocks: List[Dict]) -> Dict:
        """
        Simple consensus detection: look for repeated key phrases
        across multiple sources. High repetition = high consensus.
        """
        from collections import Counter
        import re

        # Extract 2-3 word phrases from all content
        all_text = " ".join(b["text"].lower() for b in blocks)
        words = re.findall(r"\b\w+\b", all_text)

        # Count bigrams
        bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words)-1)]
        common_bigrams = Counter(bigrams).most_common(10)

        return {
            "common_phrases": [bg for bg, count in common_bigrams if count >= 2],
            "source_count": len(blocks),
            "consensus_strength": min(len(blocks) / 5.0, 1.0),
        }

    async def _synthesise(
        self,
        topic: str,
        blocks: List[Dict],
        consensus: Dict,
        ranked: List[SearchResult],
    ) -> tuple:
        """Use LLM to generate synthesis, key facts, and full report."""
        source_text = "\n\n".join(
            f"[Source {i+1}: {b['source']}]\n{b['text']}"
            for i, b in enumerate(blocks[:6])
        )

        prompt = f"""You are a research analyst. Research topic: "{topic}"

SOURCES ({len(blocks)} total, showing top {min(len(blocks),6)}):
{source_text[:5000]}

Produce a JSON response with these EXACT keys:
{{
  "spoken_summary": "2-3 sentence summary for voice output. Natural, conversational. No bullet points.",
  "key_facts": ["fact 1", "fact 2", "fact 3", "fact 4", "fact 5"],
  "full_report": "Markdown report with sections: ## Overview, ## Key Findings, ## Recommendation, ## Sources"
}}

Rules:
- spoken_summary must be under 60 words, natural speech
- key_facts must be concrete facts, not opinions
- full_report must mention specific products/numbers/names where available
- If sources conflict, note it
"""
        try:
            loop = asyncio.get_event_loop()
            client = self._get_client()

            def _call():
                return client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_tokens=1200,
                    response_format={"type": "json_object"}
                )

            resp = await loop.run_in_executor(None, _call)
            raw = resp.choices[0].message.content.strip()
            data = json.loads(raw)

            synthesis  = data.get("spoken_summary", f"Research on {topic} complete.")
            key_facts  = data.get("key_facts", [])
            full_report = data.get("full_report", "")

            return synthesis, key_facts, full_report

        except Exception as e:
            logger.error(f"Synthesis LLM failed: {e}")
            # Fallback: use top snippet as synthesis
            synthesis = blocks[0]["text"][:200] if blocks else f"Research on {topic} complete."
            return synthesis, [], ""

    def _estimate_confidence(
        self, ranked: List[SearchResult], consensus: Dict
    ) -> float:
        if not ranked:
            return 0.0
        avg_score = sum(r.final_score for r in ranked) / len(ranked)
        consensus_boost = consensus.get("consensus_strength", 0.0) * 0.2
        return min(avg_score + consensus_boost, 1.0)
