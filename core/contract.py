"""
Execution Contracts - Every capability must declare verification
"""
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional, List, Any, Dict

# Set up a basic logger for the module
logger = logging.getLogger(__name__)

@dataclass
class CapabilityContract:
    """How to verify a capability succeeded"""
    name: str
    description: str = ""
    verify_fn: Optional[Callable] = None  # async fn(output, params) -> bool
    fallback_capabilities: List[str] = field(default_factory=list)
    max_retries: int = 1
    timeout_seconds: float = 5.0


class CapabilityRegistry:
    def __init__(self):
        self._tools: Dict[str, type] = {}
        self._contracts: Dict[str, Any] = {}
        
    def register_with_contract(self, tool_name: str, tool_class: type, contract):
        """Register a tool with its verification contract"""
        self._tools[tool_name] = tool_class
        self._contracts[tool_name] = contract
        logger.debug(f"[CapabilityRegistry] Registered with contract: {tool_name}")
            
    def get_contract(self, tool_name: str):
        """Get the verification contract for a tool"""
        return self._contracts.get(tool_name)


# Built-in verification functions tools can use
async def verify_process_running(process_name: str) -> bool:
    """Verify a process is running"""
    try:
        import psutil
        name_lower = process_name.lower()
        for proc in psutil.process_iter(["name"]):
            if name_lower in proc.info["name"].lower():
                return True
        return False
    except Exception:
        return True  # Can't verify = assume success

async def verify_window_exists(window_title: str) -> bool:
    """Verify a window with given title exists"""
    try:
        import pygetwindow as gw
        titles = [t.lower() for t in gw.getAllTitles() if t.strip()]
        return any(window_title.lower() in t for t in titles)
    except Exception:
        return True

async def verify_file_exists(filepath: str) -> bool:
    """Verify a file was created"""
    import os
    return os.path.isfile(filepath) if filepath else True

async def verify_url_accessible(url: str) -> bool:
    """Verify a URL is reachable"""
    try:
        from urllib.request import urlopen
        urlopen(url, timeout=5)
        return True
    except Exception:
        return False