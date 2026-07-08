"""
Minimal Capability Registry
Replaces hardcoded ToolRegistry._create_tool() if/elif chain
with dynamic registration.
"""
from typing import Dict, Any, Callable, Optional, List
import logging

logger = logging.getLogger(__name__)


class CapabilityRegistry:
    def __init__(self):
        self._tools: Dict[str, type] = {}
        self._contracts: Dict[str, Any] = {}  # Add this line
        
    def register_with_contract(self, tool_name: str, tool_class: type, contract):
        """Register a tool with its verification contract"""
        self._tools[tool_name] = tool_class
        self._contracts[tool_name] = contract
        
    def get_contract(self, tool_name: str):
        """Get the verification contract for a tool"""
        return self._contracts.get(tool_name)
        
    def register(self, tool_name: str, tool_class: type):
        """
        Register a tool class under a name.
        Call this ONCE per tool at module load time.
        """
        self._tools[tool_name] = tool_class
        logger.debug(f"[CapabilityRegistry] Registered: {tool_name}")
        
    def create(self, tool_name: str) -> Any:
        """Instantiate a registered tool by name."""
        tool_class = self._tools.get(tool_name)
        if tool_class is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        return tool_class()
    
    def has(self, tool_name: str) -> bool:
        """Check if a tool is registered."""
        return tool_name in self._tools
    
    def list_tools(self) -> List[str]:
        """List all registered tool names."""
        return list(self._tools.keys())


# Global instance - tools will register with this
registry = CapabilityRegistry()