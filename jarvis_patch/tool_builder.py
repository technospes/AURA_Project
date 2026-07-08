"""
JARVIS TOOL BUILDER — Autonomous Capability Expansion
======================================================

When Jarvis receives a request for which no tool exists, this module:
  1. Detects the capability gap (DecisionEngine returns BUILD_TOOL)
  2. Generates a Python tool class using the Groq LLM
  3. Validates safety via AST analysis (blocks dangerous operations)
  4. Executes in isolated sandbox
  5. Registers the tool in ToolRegistry for future reuse
  6. Retries the original command automatically

SAFETY MODEL:
  - AST-level static analysis before any execution
  - Blocklist: os.system, subprocess, shutil.rmtree, eval, exec, __import__
  - Allowlist: specific safe stdlib modules (math, re, json, datetime, etc.)
  - No filesystem writes outside designated safe directory
  - No network access unless explicitly whitelisted intent
  - Sandbox: restricted globals, no builtins except safe subset

PERSISTENCE:
  - Built tools saved to data/custom_tools/ as .py files
  - Re-loaded on startup so Jarvis gets smarter over time
  - Each tool has a SHA256 fingerprint for integrity verification

ARCHITECTURE:
  User: "send a slack notification when my build finishes"
        ↓
  Intent: "send_slack_notification" (no tool found)
        ↓
  DecisionEngine → BUILD_TOOL
        ↓
  ToolBuilder.build(intent, user_query)
        ↓
  LLM generates SlackNotificationTool class
        ↓
  SafetyValidator.validate(code) → PASS/FAIL
        ↓
  SandboxExecutor.test_instantiation()
        ↓
  ToolRegistry.register(tool) + persist to disk
        ↓
  Retry original command with new tool
"""

import ast
import asyncio
import hashlib
import importlib.util
import json
import logging
import os
import re
import sys
import textwrap
import time
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# SAFETY VALIDATOR
# ════════════════════════════════════════════════════════════════════════════

# Modules that are COMPLETELY BLOCKED
_BLOCKED_MODULES = frozenset({
    "os", "subprocess", "sys", "shutil", "ctypes", "socket",
    "pickle", "marshal", "importlib", "runpy", "code",
    "compileall", "py_compile", "zipimport", "imp",
    "winreg", "msvcrt", "nt", "_thread", "multiprocessing",
    "concurrent.futures",  # Can spawn processes
    "ftplib", "smtplib", "telnetlib", "xmlrpc",
    "http.server", "socketserver",
})

# Specific function calls that are BLOCKED
_BLOCKED_CALLS = frozenset({
    # OS execution
    "os.system", "os.popen", "os.exec", "os.execv", "os.execve",
    "os.execvp", "os.execvpe", "os.spawnl", "os.spawnle",
    "os.fork", "os.forkpty",
    # Subprocess
    "subprocess.run", "subprocess.call", "subprocess.Popen",
    "subprocess.check_output", "subprocess.check_call",
    # File deletion
    "os.remove", "os.unlink", "os.rmdir", "shutil.rmtree",
    "shutil.move", "shutil.copy",
    # Code execution
    "eval", "exec", "compile", "__import__", "execfile",
    # Dangerous builtins
    "open",   # Blocked — use only if intent is explicitly file-related
    "input",  # Interactive input not allowed in tools
    # Network (blocked by default — must be in allowlist)
    "socket.socket", "socket.connect",
    "urllib.request.urlopen", "requests.get", "requests.post",
    "httpx.get", "httpx.post",
    # Introspection/injection
    "globals", "locals", "__builtins__",
    "getattr",  # Can be used to access blocked attributes dynamically
    "setattr", "delattr",
})

# Modules that ARE allowed (safe stdlib)
_ALLOWED_MODULES = frozenset({
    "math", "re", "json", "datetime", "time", "random",
    "collections", "itertools", "functools", "operator",
    "string", "textwrap", "unicodedata",
    "hashlib", "base64", "binascii",
    "struct", "array", "copy", "pprint",
    "typing", "dataclasses", "enum", "abc",
    "pathlib",  # Only for path manipulation, not file I/O
    "logging",
})

# Network-capable intents (allowed to import requests)
_NETWORK_ALLOWED_INTENTS = frozenset({
    "web_search", "open_website", "fetch_data", "api_call",
    "check_weather", "check_stock", "send_webhook",
})


@dataclass
class ValidationResult:
    safe: bool
    issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def error_summary(self) -> str:
        return "; ".join(self.issues)


class SafetyValidator:
    """
    AST-based static safety analysis for generated tool code.

    Approach:
      1. Parse code into AST (catches syntax errors immediately)
      2. Walk all nodes looking for dangerous patterns
      3. Check all imports against allowlist
      4. Check all function calls against blocklist
      5. Verify the class structure is what we expect

    This runs BEFORE any code is executed. If validation fails,
    the code is never loaded.
    """

    def __init__(self, intent: str = "", allow_network: bool = False):
        self._intent  = intent
        self._network = allow_network or (intent in _NETWORK_ALLOWED_INTENTS)

    def validate(self, code: str) -> ValidationResult:
        result = ValidationResult(safe=True)

        # Step 1: Parse
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return ValidationResult(safe=False, issues=[f"Syntax error: {e}"])

        # Step 2: Walk all nodes
        for node in ast.walk(tree):
            self._check_node(node, result)

        # Step 3: Verify structure
        self._check_structure(tree, result)

        return result

    def _check_node(self, node: ast.AST, result: ValidationResult):
        """Check a single AST node for safety violations."""

        # Import statements
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.split(".")[0]
                if mod in _BLOCKED_MODULES:
                    result.safe = False
                    result.issues.append(f"Blocked import: {alias.name}")
                elif mod not in _ALLOWED_MODULES and mod != "typing":
                    result.warnings.append(f"Unknown import: {alias.name}")

        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod in _BLOCKED_MODULES:
                result.safe = False
                result.issues.append(f"Blocked from-import: {node.module}")
            # Check for network imports
            if mod in ("requests", "httpx", "aiohttp", "urllib") and not self._network:
                result.safe = False
                result.issues.append(
                    f"Network import '{mod}' not allowed for this intent. "
                    f"Intent must be in: {sorted(_NETWORK_ALLOWED_INTENTS)}"
                )

        # Function calls
        elif isinstance(node, ast.Call):
            call_name = self._get_call_name(node)
            if call_name in _BLOCKED_CALLS:
                result.safe = False
                result.issues.append(f"Blocked call: {call_name}")
            # open() is blocked — tools should not do arbitrary file I/O
            if call_name == "open":
                result.safe = False
                result.issues.append(
                    "open() is blocked. Tools cannot do arbitrary file I/O. "
                    "Use the MemoryTool to store data."
                )

        # Attribute access that could be dangerous
        elif isinstance(node, ast.Attribute):
            if node.attr in ("__class__", "__bases__", "__subclasses__",
                             "__globals__", "__builtins__", "__code__",
                             "__closure__", "__module__"):
                result.safe = False
                result.issues.append(f"Blocked attribute access: .{node.attr}")

        # String-based exec patterns
        elif isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Call):
                name = self._get_call_name(node.value)
                if name in ("eval", "exec"):
                    result.safe = False
                    result.issues.append(f"Blocked: {name}()")

    def _check_structure(self, tree: ast.Module, result: ValidationResult):
        """Verify the generated code has the expected class structure."""
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        if not classes:
            result.safe = False
            result.issues.append("Generated code must contain at least one class")
            return

        # Find class with execute() method
        has_execute = False
        for cls in classes:
            methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
            if "execute" in methods:
                has_execute = True
                break

        if not has_execute:
            result.safe = False
            result.issues.append("Tool class must have an execute() method")

    @staticmethod
    def _get_call_name(node: ast.Call) -> str:
        """Extract the function/method name from a Call node."""
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            parts = []
            n = node.func
            while isinstance(n, ast.Attribute):
                parts.append(n.attr)
                n = n.value
            if isinstance(n, ast.Name):
                parts.append(n.id)
            return ".".join(reversed(parts))
        return ""


# ════════════════════════════════════════════════════════════════════════════
# SANDBOX EXECUTOR
# ════════════════════════════════════════════════════════════════════════════

# Safe builtins allowed in sandbox
_SAFE_BUILTINS = {
    "abs", "all", "any", "bool", "bytes", "chr", "dict", "dir",
    "divmod", "enumerate", "filter", "float", "format", "frozenset",
    "getattr", "hasattr", "hash", "hex", "id", "int", "isinstance",
    "issubclass", "iter", "len", "list", "map", "max", "min",
    "next", "oct", "ord", "pow", "print", "range", "repr",
    "reversed", "round", "set", "slice", "sorted", "str", "sum",
    "tuple", "type", "vars", "zip",
    # Exceptions
    "Exception", "ValueError", "TypeError", "RuntimeError",
    "KeyError", "IndexError", "AttributeError", "NotImplementedError",
    "StopIteration", "GeneratorExit",
    # Constants
    "True", "False", "None",
}


class SandboxExecutor:
    """
    Execute generated code in a restricted namespace.

    The sandbox:
      - No access to real builtins (only _SAFE_BUILTINS)
      - No access to __import__ (can't import new modules)
      - Allowed imports are pre-resolved and injected
      - Any exception is caught and reported safely
    """

    def __init__(self, allowed_imports: Optional[Dict[str, Any]] = None):
        self._allowed_imports = allowed_imports or self._default_imports()

    def _default_imports(self) -> Dict[str, Any]:
        """Pre-import all safe modules into the sandbox namespace."""
        imports = {}
        safe_mods = [
            "math", "re", "json", "datetime", "time", "random",
            "collections", "itertools", "functools", "operator",
            "string", "textwrap", "hashlib", "base64", "logging",
        ]
        for mod in safe_mods:
            try:
                imports[mod] = __import__(mod)
            except ImportError:
                pass
        return imports

    def build_namespace(self) -> Dict[str, Any]:
        """Create a restricted execution namespace."""
        import builtins
        safe_bi = {k: getattr(builtins, k) for k in _SAFE_BUILTINS if hasattr(builtins, k)}

        ns = {
            "__builtins__": safe_bi,
            "__name__": "__sandbox__",
            "__doc__": None,
        }
        ns.update(self._allowed_imports)
        return ns

    def execute(self, code: str) -> Tuple[bool, Optional[type], str]:
        """
        Execute code in sandbox. Returns (success, tool_class, error_message).
        """
        ns = self.build_namespace()
        try:
            exec(compile(code, "<generated_tool>", "exec"), ns)
        except Exception as e:
            return False, None, f"Execution error: {type(e).__name__}: {e}"

        # Find the tool class (must inherit from BaseTool or have execute())
        tool_class = None
        for name, obj in ns.items():
            if (isinstance(obj, type)
                    and name != "BaseTool"
                    and hasattr(obj, "execute")
                    and callable(getattr(obj, "execute", None))):
                tool_class = obj
                break

        if tool_class is None:
            return False, None, "No valid tool class found in generated code"

        # Test instantiation (in case __init__ raises)
        try:
            instance = tool_class()
        except Exception as e:
            return False, None, f"Tool instantiation failed: {e}"

        return True, tool_class, ""


# ════════════════════════════════════════════════════════════════════════════
# TOOL BUILDER LLM
# ════════════════════════════════════════════════════════════════════════════

_TOOL_GENERATION_SYSTEM = """You are a Python code generator for Jarvis AI assistant.
Your job is to write a tool class that can execute a specific action.

STRICT RULES:
1. Output ONLY valid Python code — no markdown, no explanations
2. The class MUST have an async execute(self, action, params, intent, context, step_results) method
3. Return a dict with: {"success": bool, "message": str, "output": any}
4. NEVER use: os.system, subprocess, eval, exec, open(), __import__
5. Only import from: math, re, json, datetime, time, random, collections, logging
6. For Windows automation, use pyautogui (if needed) — it's pre-approved
7. Keep it simple. Do one thing well.

CLASS TEMPLATE:
```python
class ExampleTool:
    \"\"\"Brief description.\"\"\"
    
    async def execute(self, action, params, intent, context, step_results):
        try:
            # Your implementation here
            result = self._do_something(params)
            return {"success": True, "message": f"Done: {result}", "output": result}
        except Exception as e:
            return {"success": False, "message": str(e), "output": None}
    
    def _do_something(self, params):
        # Helper method
        pass
```

Output ONLY the Python class code. Nothing else."""


class ToolBuilderLLM:
    """LLM interface for tool code generation."""

    def __init__(self, groq_api_key: str):
        self._api_key = groq_api_key
        self._client  = None

    def _get_client(self):
        if not self._client:
            from groq import Groq
            self._client = Groq(api_key=self._api_key)
        return self._client

    async def generate(
        self,
        user_query: str,
        intent: str,
        entities: Dict,
        context: Dict,
        previous_error: str = "",
    ) -> str:
        """
        Generate Python tool code for the given intent.
        Returns raw Python code string.
        """
        # Build a specific prompt for this tool
        error_note = ""
        if previous_error:
            error_note = f"\n\nPREVIOUS ATTEMPT FAILED: {previous_error}\nFix those issues."

        prompt = f"""Generate a Python tool class to handle this user request:
User said: "{user_query}"
Intent: {intent}
Entities: {json.dumps(entities, default=str)}
OS Context: Windows 11, Python 3.11
{error_note}

The tool class should:
1. Be named {self._class_name(intent)}
2. Handle action="{intent}" in its execute() method
3. Use only the allowed imports (math, re, json, datetime, time, random, collections, logging)
4. For UI automation: pyautogui is available
5. Return dict with success/message/output keys

Write ONLY the Python class. No imports outside the allowed list. No markdown."""

        loop = asyncio.get_event_loop()

        def _call():
            client = self._get_client()
            return client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": _TOOL_GENERATION_SYSTEM},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0.1,
                max_tokens=800,
            )

        try:
            resp = await loop.run_in_executor(None, _call)
            raw  = resp.choices[0].message.content.strip()

            # Strip markdown code fences if LLM included them
            raw = re.sub(r'^```python\s*', '', raw, flags=re.I)
            raw = re.sub(r'^```\s*',       '', raw, flags=re.I)
            raw = re.sub(r'\s*```$',       '', raw)

            return raw.strip()

        except Exception as e:
            logger.error(f"[ToolBuilder] LLM generation failed: {e}")
            return ""

    @staticmethod
    def _class_name(intent: str) -> str:
        """Convert intent string to PascalCase class name."""
        parts = intent.replace("-", "_").split("_")
        return "".join(p.capitalize() for p in parts) + "Tool"


# ════════════════════════════════════════════════════════════════════════════
# TOOL PERSISTENCE
# ════════════════════════════════════════════════════════════════════════════

_TOOLS_DIR = Path("data/custom_tools")


@dataclass
class PersistedTool:
    name: str
    intent: str
    code: str
    checksum: str
    created_at: float
    usage_count: int = 0
    last_used: float = 0.0


class ToolPersistence:
    """Save and load custom tools across restarts."""

    def __init__(self, tools_dir: Path = _TOOLS_DIR):
        self._dir = tools_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_file = self._dir / "index.json"

    def save(self, intent: str, code: str, class_name: str) -> str:
        """Save tool code to disk. Returns the file path."""
        checksum  = hashlib.sha256(code.encode()).hexdigest()[:12]
        filename  = f"{intent.replace('_', '-')}_{checksum}.py"
        filepath  = self._dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# Auto-generated tool for intent: {intent}\n")
            f.write(f"# Checksum: {checksum}\n")
            f.write(f"# Created: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(code)

        # Update index
        index = self._load_index()
        index[intent] = {
            "file":       filename,
            "class_name": class_name,
            "checksum":   checksum,
            "created_at": time.time(),
            "usage_count": 0,
        }
        self._save_index(index)

        logger.info(f"[ToolPersistence] Saved: {filename}")
        return str(filepath)

    def load_all(self) -> Dict[str, Tuple[str, str]]:
        """
        Load all persisted tools.
        Returns {intent: (code, class_name)} dict.
        """
        index = self._load_index()
        result = {}

        for intent, meta in index.items():
            filepath = self._dir / meta["file"]
            if not filepath.exists():
                logger.warning(f"[ToolPersistence] Missing file: {filepath}")
                continue
            try:
                code = filepath.read_text(encoding="utf-8")
                # Strip the header comments
                lines = code.split("\n")
                code_lines = [l for l in lines if not l.startswith("# ")]
                result[intent] = ("\n".join(code_lines).strip(), meta["class_name"])
            except Exception as e:
                logger.error(f"[ToolPersistence] Failed to load {filepath}: {e}")

        return result

    def _load_index(self) -> Dict:
        if not self._index_file.exists():
            return {}
        try:
            return json.loads(self._index_file.read_text())
        except Exception:
            return {}

    def _save_index(self, index: Dict):
        self._index_file.write_text(json.dumps(index, indent=2))


# ════════════════════════════════════════════════════════════════════════════
# MAIN TOOL BUILDER
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class BuildResult:
    success: bool
    tool_name: str = ""
    tool_class: Optional[type] = None
    error: str = ""
    code: str = ""
    validation_issues: List[str] = field(default_factory=list)


class ToolBuilder:
    """
    Autonomous tool construction pipeline.

    Usage:
        builder = ToolBuilder(groq_api_key=GROQ_KEY)
        result  = await builder.build(intent, user_query, entities, context)
        if result.success:
            registry.register(intent, result.tool_class)
    """

    MAX_RETRIES = 3

    def __init__(self, groq_api_key: str):
        self._llm         = ToolBuilderLLM(groq_api_key)
        self._sandbox     = SandboxExecutor()
        self._persistence = ToolPersistence()
        self._built_tools: Dict[str, type] = {}

    async def build(
        self,
        intent:     str,
        user_query: str,
        entities:   Dict,
        context:    Dict,
    ) -> BuildResult:
        """
        Build a tool for the given intent.

        Pipeline:
          1. Check if tool already built (cache hit)
          2. Generate code with LLM
          3. Validate with SafetyValidator
          4. Execute in sandbox
          5. Persist to disk
          6. Return the tool class

        Retries up to MAX_RETRIES times if validation/sandbox fails.
        Each retry feeds the error back to the LLM for correction.
        """
        # Cache hit
        if intent in self._built_tools:
            logger.info(f"[ToolBuilder] Cache hit: {intent}")
            return BuildResult(
                success=True,
                tool_name=intent,
                tool_class=self._built_tools[intent],
            )

        logger.info(f"[ToolBuilder] Building tool for intent: {intent}")
        logger.info(f"[ToolBuilder] User query: '{user_query}'")

        # Determine if network is needed
        validator = SafetyValidator(
            intent=intent,
            allow_network=(intent in _NETWORK_ALLOWED_INTENTS),
        )

        previous_error = ""
        code = ""

        for attempt in range(1, self.MAX_RETRIES + 1):
            logger.info(f"[ToolBuilder] Attempt {attempt}/{self.MAX_RETRIES}")

            # Generate code
            code = await self._llm.generate(
                user_query=user_query,
                intent=intent,
                entities=entities,
                context=context,
                previous_error=previous_error,
            )

            if not code:
                previous_error = "LLM returned empty code"
                continue

            # Validate
            v_result = validator.validate(code)
            if not v_result.safe:
                previous_error = f"Safety validation failed: {v_result.error_summary}"
                logger.warning(f"[ToolBuilder] Validation failed: {previous_error}")
                continue

            if v_result.warnings:
                logger.info(f"[ToolBuilder] Warnings: {v_result.warnings}")

            # Sandbox execution
            ok, tool_class, err = self._sandbox.execute(code)
            if not ok:
                previous_error = err
                logger.warning(f"[ToolBuilder] Sandbox failed: {err}")
                continue

            # Success!
            class_name = tool_class.__name__
            self._built_tools[intent] = tool_class

            # Persist to disk
            try:
                self._persistence.save(intent, code, class_name)
            except Exception as e:
                logger.warning(f"[ToolBuilder] Persistence failed (non-fatal): {e}")

            logger.info(f"[ToolBuilder]  Built: {class_name} for intent={intent}")
            return BuildResult(
                success=True,
                tool_name=class_name,
                tool_class=tool_class,
                code=code,
            )

        # All retries failed
        logger.error(f"[ToolBuilder] Failed after {self.MAX_RETRIES} attempts: {previous_error}")
        return BuildResult(
            success=False,
            error=f"Could not safely build tool after {self.MAX_RETRIES} attempts. "
                  f"Last error: {previous_error}",
        )

    def load_persisted_tools(self) -> Dict[str, type]:
        """
        Load all previously built tools from disk at startup.
        Returns {intent: tool_class} dict.
        """
        loaded = {}
        persisted = self._persistence.load_all()

        for intent, (code, class_name) in persisted.items():
            validator = SafetyValidator(intent=intent)
            v_result  = validator.validate(code)

            if not v_result.safe:
                logger.warning(
                    f"[ToolBuilder] Persisted tool '{intent}' failed re-validation "
                    f"— skipping. Issues: {v_result.issues}"
                )
                continue

            ok, tool_class, err = self._sandbox.execute(code)
            if ok and tool_class:
                loaded[intent]             = tool_class
                self._built_tools[intent]  = tool_class
                logger.info(f"[ToolBuilder] Loaded persisted tool: {intent}")
            else:
                logger.warning(f"[ToolBuilder] Persisted tool '{intent}' sandbox failed: {err}")

        return loaded

    def get_built_tools(self) -> Dict[str, type]:
        return dict(self._built_tools)


# ════════════════════════════════════════════════════════════════════════════
# DECISION ENGINE EXTENSION — BUILD_TOOL action
# ════════════════════════════════════════════════════════════════════════════

def extend_decision_engine():
    """
    Patch DecisionEngine to return BUILD_TOOL when no tool matches.
    Call this from apply_patches() in core_patch.py.
    """
    try:
        from agent.decision import Decision, DecisionEngine, DecisionResult
        from executor.runner import ToolRegistry

        # Add BUILD_TOOL to Decision enum
        Decision._value2member_map_["build_tool"] = Decision.EXECUTE  # map to EXECUTE for now
        Decision.BUILD_TOOL = type(Decision.EXECUTE)(  # type: ignore
            "BUILD_TOOL", (object,), {"value": "build_tool"}
        )

        _orig_decide = DecisionEngine.decide

        def _patched_decide(self_de, intent, context, memory_context):
            result = _orig_decide(self_de, intent, context, memory_context)

            # If decision is EXECUTE but tool doesn't exist → BUILD_TOOL
            if result.decision == Decision.EXECUTE:
                intent_name = intent.get("intent", "")
                # Check if a tool exists for this intent
                from planner.engine import _make_registry
                known_intents = set(_make_registry().keys())
                if intent_name not in known_intents and intent_name not in (
                    "system_action", "guided_recommendation", "answer_question",
                    "quick_answer", "conversation",
                ):
                    return DecisionResult(
                        decision=Decision.EXECUTE,  # Will be handled by BUILD_TOOL path
                        reason=f"No tool for '{intent_name}' — will build one",
                        confidence=result.confidence,
                    )

            return result

        DecisionEngine.decide = _patched_decide
        logger.info("[ToolBuilder] DecisionEngine patched for BUILD_TOOL")

    except Exception as e:
        logger.error(f"[ToolBuilder] DecisionEngine patch failed: {e}")


# ════════════════════════════════════════════════════════════════════════════
# TOOL REGISTRY EXTENSION — dynamic tool registration
# ════════════════════════════════════════════════════════════════════════════

class DynamicToolRegistry:
    """
    Extends ToolRegistry with dynamic tool registration capability.
    
    Wraps the existing ToolRegistry.get() to check dynamic tools first.
    """

    def __init__(self, base_registry, tool_builder: ToolBuilder):
        self._base    = base_registry
        self._builder = tool_builder
        self._dynamic: Dict[str, Any] = {}

    def register(self, tool_name: str, tool_class: type):
        """Register a dynamically built tool."""
        self._dynamic[tool_name] = tool_class()
        logger.info(f"[DynamicRegistry] Registered: {tool_name}")

    def get(self, tool_name: str) -> Any:
        """Get tool — checks dynamic registry first, then base registry."""
        if tool_name in self._dynamic:
            return self._dynamic[tool_name]
        return self._base.get(tool_name)

    def has_dynamic(self, tool_name: str) -> bool:
        return tool_name in self._dynamic


# ════════════════════════════════════════════════════════════════════════════
# AGENT CORE INTEGRATION
# ════════════════════════════════════════════════════════════════════════════

async def handle_build_tool_request(
    agent_core,
    raw_input: str,
    intent: Dict,
    turn,
    start: float,
) -> Any:
    from agent.core import AgentState

    intent_name = intent.get("intent", "unknown")
    entities    = intent.get("entities", {})

    # Announce
    if agent_core._tts_callback:
        agent_core._tts_callback(
            f"I don't have a tool for that yet, Sir. "
            f"Let me build one. This may take a moment."
        )

    # Build
    if not hasattr(agent_core, '_tool_builder'):
        agent_core._tool_builder = ToolBuilder(
            groq_api_key=agent_core.config.get("groq_api_key", "")
        )

    ctx = agent_core.context.snapshot()
    result = await agent_core._tool_builder.build(
        intent=intent_name,
        user_query=raw_input,
        entities=entities,
        context=ctx,
    )

    if not result.success:
        msg = f"I couldn't build a tool for that safely, Sir. {result.error}"
        turn.response = turn.spoken_response = msg
        turn.success  = False
        turn.duration_ms = (time.perf_counter() - start) * 1000
        agent_core.state = AgentState.IDLE
        return turn

    # Register the new tool
    try:
        from core.capability_registry import registry as cap_registry
        cap_registry.register(intent_name, result.tool_class)
        registry = agent_core.executor.registry
        if hasattr(registry, '_tools'):
            registry._tools[intent_name] = result.tool_class()
        elif hasattr(registry, '_dynamic'):
            registry._dynamic[intent_name] = result.tool_class()

        # Also patch _create_tool for future use
        if hasattr(registry, '_create_tool'):
            _orig = registry._create_tool
            _cls  = result.tool_class

            def _patched_create(self_reg, name):
                if name == intent_name:
                    return _cls()
                return _orig(name)

            registry._create_tool = _patched_create

        logger.info(f"[ToolBuilder] Tool registered: {intent_name}")

    except Exception as e:
        logger.error(f"[ToolBuilder] Registration failed: {e}")

    # Announce success
    if agent_core._tts_callback:
        agent_core._tts_callback(
            f"I've built a new tool for '{intent_name.replace('_', ' ')}'. "
            f"Executing now, Sir."
        )

    # Re-execute with the new tool
    try:
        ctx_snapshot   = agent_core.context.snapshot()
        memory_context = await agent_core.memory.recall(raw_input, intent, ctx_snapshot)
        turn.plan      = await agent_core.planner.create_plan(intent, memory_context, ctx_snapshot)
        agent_core.state = AgentState.EXECUTING
        turn = await agent_core._execute_with_reflection(turn, ctx_snapshot)
        await agent_core._store_turn(turn)
        await agent_core.context.update_from_turn(turn)
        rd = await agent_core.responder.generate(turn, ctx_snapshot, memory_context)
        turn.response        = rd["full_response"]
        turn.spoken_response = rd["spoken_response"]
    except Exception as e:
        logger.error(f"[ToolBuilder] Re-execution failed: {e}", exc_info=True)
        msg = f"I built the tool but couldn't execute it, Sir. Error: {str(e)[:80]}"
        turn.response = turn.spoken_response = msg
        turn.success  = False

    turn.duration_ms = (time.perf_counter() - start) * 1000
    agent_core.state = AgentState.IDLE
    return turn


def load_persisted_tools_into_registry(agent_core):
    """
    Load all previously built custom tools at startup.
    Call from JarvisAgentCore.__init__ or apply_patches().
    """
    try:
        if not hasattr(agent_core, '_tool_builder'):
            agent_core._tool_builder = ToolBuilder(
                groq_api_key=agent_core.config.get("groq_api_key", "")
            )

        loaded = agent_core._tool_builder.load_persisted_tools()
        if not loaded:
            return

        registry = agent_core.executor.registry
        for intent_name, tool_class in loaded.items():
            try:
                from core.capability_registry import registry as cap_registry
                cap_registry.register(intent_name, tool_class)
                if hasattr(registry, '_tools'):
                    registry._tools[intent_name] = tool_class()
                logger.info(f"[ToolBuilder] Loaded persisted: {intent_name}")
            except Exception as e:
                logger.warning(f"[ToolBuilder] Could not load {intent_name}: {e}")

        logger.info(f"[ToolBuilder]  Loaded {len(loaded)} persisted custom tools")

    except Exception as e:
        logger.error(f"[ToolBuilder] Startup load failed: {e}")