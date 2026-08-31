"""Shell-composable CLI for every DaVinci Resolve MCP surface.

The CLI intentionally dispatches through FastMCP's registered Tool objects.
That keeps argument validation and implementations identical to MCP while
removing the JSON-RPC/chat transport from terminal workflows.
"""
from __future__ import annotations

import ast
import asyncio
import base64
import inspect
import json
import os
import re
import shlex
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, TextIO


EXIT_OK = 0
EXIT_TOOL_ERROR = 1
EXIT_USAGE = 2
EXIT_INTERNAL = 3
EXIT_INTERRUPTED = 130

VERSION = "2.103.2"
SURFACES = ("compound", "granular")
OUTPUTS = ("json", "jsonl", "raw", "shell")


class CliUsageError(ValueError):
    """A command-line or input document could not be parsed."""


def _fastmcp(surface: str):
    if surface == "compound":
        from src import server

        return server.mcp
    if surface == "granular":
        from src.granular import mcp

        return mcp
    raise CliUsageError(f"unknown surface {surface!r}; use compound or granular")


def build_registry(surface: str = "compound") -> Dict[str, Any]:
    """Return the actual registered FastMCP Tool mapping (stable audit API)."""
    manager = getattr(_fastmcp(surface), "_tool_manager", None)
    tools = getattr(manager, "_tools", None)
    if not isinstance(tools, dict):
        raise RuntimeError(f"FastMCP {surface} tool registry is unavailable")
    return tools


def build_prompt_registry(surface: str = "compound") -> Dict[str, Any]:
    manager = getattr(_fastmcp(surface), "_prompt_manager", None)
    prompts = getattr(manager, "_prompts", None)
    if not isinstance(prompts, dict):
        raise RuntimeError(f"FastMCP {surface} prompt registry is unavailable")
    return prompts


def build_resource_registry(surface: str = "compound") -> Dict[str, Any]:
    manager = getattr(_fastmcp(surface), "_resource_manager", None)
    resources = getattr(manager, "_resources", None)
    if not isinstance(resources, dict):
        raise RuntimeError(f"FastMCP {surface} resource registry is unavailable")
    return resources


def build_resource_template_registry(surface: str = "compound") -> Dict[str, Any]:
    manager = getattr(_fastmcp(surface), "_resource_manager", None)
    templates = getattr(manager, "_templates", None)
    if not isinstance(templates, dict):
        raise RuntimeError(f"FastMCP {surface} resource-template registry is unavailable")
    return templates


async def call_registered_tool(surface: str, name: str, arguments: Dict[str, Any]) -> Any:
    tool = build_registry(surface).get(name)
    if tool is None:
        raise CliUsageError(f"unknown {surface} tool: {name}")
    return await tool.run(arguments, convert_result=False)


async def render_registered_prompt(
    surface: str, name: str, arguments: Optional[Dict[str, Any]] = None
) -> Any:
    if name not in build_prompt_registry(surface):
        raise CliUsageError(f"unknown {surface} prompt: {name}")
    return await _fastmcp(surface).get_prompt(name, arguments or {})


async def read_registered_resource(surface: str, uri: str) -> Any:
    # FastMCP resolves concrete resources and URI templates in one call.
    return await _fastmcp(surface).read_resource(uri)


def _annotation_dict(value: Any) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if hasattr(value, "dict"):
        return value.dict(exclude_none=True)
    return normalize(value)


def list_tools(surface: str = "compound") -> List[Dict[str, Any]]:
    rows = []
    for name, tool in sorted(build_registry(surface).items()):
        rows.append(
            {
                "name": name,
                "surface": surface,
                "description": (tool.description or "").strip(),
                "input_schema": tool.parameters,
                "annotations": _annotation_dict(getattr(tool, "annotations", None)),
            }
        )
    return rows


def _description(tool: Any, surface: str) -> Dict[str, Any]:
    return {
        "name": tool.name,
        "surface": surface,
        "description": (tool.description or "").strip(),
        "input_schema": tool.parameters,
        "output_schema": getattr(getattr(tool, "fn_metadata", None), "output_schema", None),
        "annotations": _annotation_dict(getattr(tool, "annotations", None)),
        "actions": discover_actions(tool.name) if surface == "compound" else None,
    }


def _action_doc_entry(tool: Any, action: str) -> Optional[Dict[str, Any]]:
    """Return the best action-local prose embedded in a compound tool docstring.

    Compound tools intentionally expose a permissive ``params`` object because
    each action has a different shape.  Their Actions sections are therefore
    the only action-local contract for much of the surface.  Keep the original
    signature as prose and label the derived parameter list as such instead of
    pretending it is a strict JSON schema.
    """
    doc = (tool.description or "").strip()
    if "Actions:" not in doc:
        return None
    lines = doc.split("Actions:", 1)[1].splitlines()
    known = set(discover_actions(tool.name))
    start_re = re.compile(r"^\s*(?:-\s+)?([a-z][a-z0-9_]*)\s*\((.*)$")
    start = None
    for index, line in enumerate(lines):
        match = start_re.match(line)
        if match and match.group(1) == action:
            start = index
            break
    if start is None:
        return None

    selected: List[str] = []
    for line in lines[start:]:
        match = start_re.match(line)
        if selected and match and match.group(1) in known:
            break
        selected.append(line.strip())
    prose = " ".join(part for part in selected if part).strip()
    prefix = re.match(rf"(?:-\s+)?{re.escape(action)}\s*\(", prose)
    if not prefix:
        return None

    # The first balanced close belongs to the action signature. Parentheses in
    # descriptions come afterwards; nested object hints inside the signature
    # are handled by the depth counter.
    depth = 0
    close = None
    for index, char in enumerate(prose[prefix.end() - 1 :], prefix.end() - 1):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                close = index
                break
    if close is None:
        return {"signature": prose, "summary": None, "parameters": []}
    signature = prose[: close + 1].removeprefix("- ").strip()
    summary = prose[close + 1 :].strip(" —-") or None
    inside = signature[signature.find("(") + 1 : -1]
    return {
        "signature": signature,
        "summary": summary,
        "parameters": _documented_parameters(inside),
    }


def _documented_parameters(signature: str) -> List[Dict[str, Any]]:
    """Best-effort top-level names from a documented action signature."""
    chunks: List[str] = []
    current: List[str] = []
    depth = 0
    for char in signature:
        if char in "[({":
            depth += 1
        elif char in "])}":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            chunks.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        chunks.append("".join(current).strip())

    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for chunk in chunks:
        match = re.match(
            r"([a-z][a-z0-9_]*\??(?:\s*\|\s*[a-z][a-z0-9_]*\??)*)",
            chunk,
        )
        head = match.group(1) if match else ""
        alternatives = [part.strip().rstrip("?") for part in head.split("|")]
        names = [name for name in alternatives if re.fullmatch(r"[a-z][a-z0-9_]*", name)]
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            row: Dict[str, Any] = {
                "name": name,
                # Alternatives express one accepted spelling/selector, not a
                # requirement to pass every name in the group.
                "required": len(names) == 1 and "?" not in head and "=" not in chunk,
                "source": "documented_signature",
            }
            if len(names) > 1:
                row["alternative_group"] = names
            rows.append(row)
    return rows


def describe_action(tool: Any, surface: str, action: str) -> Dict[str, Any]:
    """Describe one action without connecting to Resolve.

    Registered ``_ACTION_HELP`` entries are authoritative documentation.  Most
    actions predate that catalog, so the tool's Actions prose is the fallback.
    The returned schema is explicitly open in both cases: no strict per-action
    runtime schema exists behind FastMCP's generic ``params`` object.
    """
    if surface != "compound":
        raise CliUsageError(
            f"{surface} tool {tool.name!r} is a direct function and has no ACTION layer; "
            f"use 'dvr describe {tool.name}'"
        )
    actions = discover_actions(tool.name)
    if action not in actions:
        raise CliUsageError(f"unknown action {tool.name}.{action}")

    detailed = None
    try:
        from src import server

        entry = getattr(server, "_ACTION_HELP", {}).get(tool.name, {}).get(action)
        if isinstance(entry, dict):
            detailed = normalize(entry)
    except Exception:
        detailed = None
    documented = _action_doc_entry(tool, action)
    params_text = detailed.get("params") if detailed else None
    parameters = (
        _documented_parameters(str(params_text))
        if params_text
        else (documented or {}).get("parameters", [])
    )
    source = "registered_action_help" if detailed else (
        "tool_docstring" if documented else "action_catalog"
    )
    metadata: Dict[str, Any] = {
        "name": action,
        "source": source,
        "documented": bool(detailed or documented),
        "summary": (detailed or {}).get("summary") or (documented or {}).get("summary"),
        "signature": (documented or {}).get("signature"),
        "parameters": parameters,
        "input_schema": {
            "type": "object",
            "additionalProperties": True,
            "x-dvr-schema-confidence": "documented_open_object" if source != "action_catalog" else "unknown",
        },
    }
    if detailed:
        metadata.update({key: value for key, value in detailed.items() if key not in metadata})
    if source == "action_catalog":
        metadata["note"] = (
            "The action is registered, but no action-specific parameter contract is documented; "
            "the FastMCP runtime accepts an open params object."
        )
    return metadata


def _eval_action_expr(node: ast.AST, namespace: Dict[str, Any]) -> List[str]:
    try:
        value = eval(compile(ast.Expression(node), "<action-catalog>", "eval"), namespace)
    except Exception:
        return []
    return [str(item) for item in value] if isinstance(value, (list, tuple, set)) else []


def _doc_actions(doc: str) -> List[str]:
    """Extract function-like entries from the docstring's Actions section."""
    if not doc or "Actions:" not in doc:
        return []
    section = doc.split("Actions:", 1)[1]
    # Stop at the next unindented prose heading when there is one.
    section = re.split(r"\n\s*\n(?=[A-Z][^\n]{0,60}:?\n)", section, maxsplit=1)[0]
    return re.findall(r"^\s{2,}([a-z][a-z0-9_]*)\s*\(", section, flags=re.MULTILINE)


_ACTION_CACHE: Optional[Dict[str, List[str]]] = None


def _compound_action_catalog() -> Dict[str, List[str]]:
    global _ACTION_CACHE
    if _ACTION_CACHE is not None:
        return _ACTION_CACHE
    from src import server

    source_path = Path(server.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    names = set(build_registry("compound"))
    catalog: Dict[str, List[str]] = {}
    namespace = vars(server)
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in names:
            continue
        unknown_actions: set[str] = set()
        compared_actions: set[str] = set()
        for child in ast.walk(node):
            if not isinstance(child, ast.Call) or len(child.args) < 2:
                continue
            if isinstance(child.func, ast.Name) and child.func.id == "_unknown":
                unknown_actions.update(_eval_action_expr(child.args[1], namespace))
        # An _unknown(action, [...]) list is the implementation's authoritative
        # catalog. Docstrings contain prose that looks like calls (for example
        # ``resolve_control(...)``), so mixing both creates phantom actions.
        if unknown_actions:
            actions = unknown_actions
        else:
            for child in ast.walk(node):
                if not (isinstance(child, ast.Compare) and isinstance(child.left, ast.Name) and child.left.id == "action"):
                    continue
                for comparator in child.comparators:
                    if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                        compared_actions.add(comparator.value)
                    elif isinstance(comparator, (ast.Set, ast.List, ast.Tuple)):
                        compared_actions.update(
                            item.value
                            for item in comparator.elts
                            if isinstance(item, ast.Constant) and isinstance(item.value, str)
                        )
            actions = compared_actions or set(_doc_actions(ast.get_docstring(node) or ""))
        catalog[node.name] = sorted(actions)
    _ACTION_CACHE = catalog
    return catalog


def discover_actions(tool_name: str) -> List[str]:
    return list(_compound_action_catalog().get(tool_name, []))


_ADVANCED_ACTION_CACHE: Optional[Dict[str, List[str]]] = None


def _advanced_action_catalog() -> Dict[str, List[str]]:
    """Read the packaged Node tool sources without starting Node or Resolve."""
    global _ADVANCED_ACTION_CACHE
    if _ADVANCED_ACTION_CACHE is not None:
        return _ADVANCED_ACTION_CACHE
    tools_dir = Path(__file__).resolve().parents[1] / "resolve-advanced" / "server" / "tools"
    catalog: Dict[str, List[str]] = {}
    if tools_dir.is_dir():
        for source_path in tools_dir.glob("*.mjs"):
            if source_path.name == "index.mjs":
                continue
            try:
                source = source_path.read_text(encoding="utf-8")
            except OSError:
                continue
            if not re.search(r"export\s+const\s+\w+Tool\s*=", source):
                continue
            name_match = re.search(r"\bname:\s*['\"]([^'\"]+)['\"]", source)
            if not name_match:
                continue
            actions = set(re.findall(r"\baction\s*===\s*['\"]([^'\"]+)['\"]", source))
            actions.update(re.findall(r"\bcase\s+['\"]([^'\"]+)['\"]\s*:", source))
            if name_match.group(1) == "capabilities" and not actions:
                actions.add("get")
            catalog[name_match.group(1)] = sorted(actions)
    _ADVANCED_ACTION_CACHE = catalog
    return catalog


def normalize(value: Any) -> Any:
    """Convert MCP/Pydantic/Resolve-adjacent values to lossless JSON values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {
            "$binary": base64.b64encode(value).decode("ascii"),
            "encoding": "base64",
            "size": len(value),
        }
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "to_image_content"):
        try:
            return normalize(value.to_image_content())
        except Exception:
            pass
    if hasattr(value, "model_dump"):
        return normalize(value.model_dump(mode="json", by_alias=True, exclude_none=False))
    if is_dataclass(value):
        return normalize(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): normalize(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [normalize(item) for item in value]
    if hasattr(value, "__dict__"):
        return normalize(vars(value))
    return str(value)


def _parse_scalar(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return raw


def _load_json_source(source: str) -> Any:
    if source == "-":
        raw = sys.stdin.read()
    elif source.startswith("@"):
        path = Path(source[1:]).expanduser()
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CliUsageError(f"cannot read input file {path}: {exc}") from exc
    else:
        raw = source
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CliUsageError(f"invalid JSON input: {exc}") from exc


def _put(target: Dict[str, Any], dotted_key: str, value: Any) -> None:
    key = dotted_key.strip().replace("-", "_")
    if not key:
        raise CliUsageError("parameter key cannot be empty")
    parts = [part for part in key.split(".") if part]
    cursor = target
    for part in parts[:-1]:
        existing = cursor.get(part)
        if existing is None:
            existing = {}
            cursor[part] = existing
        if not isinstance(existing, dict):
            raise CliUsageError(f"cannot assign nested key {dotted_key!r}: {part!r} is not an object")
        cursor = existing
    final = parts[-1]
    # Parameter sources compose left-to-right. Repetition is an override, just
    # like jq object multiplication and environment assignment; callers that
    # need an array pass JSON (`clips=["a","b"]`).
    cursor[final] = value


def _merge(target: Dict[str, Any], incoming: Mapping[str, Any]) -> None:
    for key, value in incoming.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, Mapping):
            _merge(target[key], value)
        else:
            target[key] = value


def parse_params(tokens: List[str], inputs: Iterable[str] = (), sets: Iterable[str] = ()) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    for source in inputs:
        payload = _load_json_source(source)
        if not isinstance(payload, dict):
            raise CliUsageError("--input must contain a JSON object")
        _merge(params, payload)
    for assignment in sets:
        if "=" not in assignment:
            raise CliUsageError(f"--set requires key=value, got {assignment!r}")
        key, raw = assignment.split("=", 1)
        _put(params, key, _parse_scalar(raw))

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--":
            i += 1
            continue
        if token.startswith("--no-") and "=" not in token:
            _put(params, token[5:], False)
            i += 1
            continue
        if token.startswith("--"):
            option = token[2:]
            if "=" in option:
                key, raw = option.split("=", 1)
                _put(params, key, _parse_scalar(raw))
                i += 1
                continue
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("--") and "=" not in tokens[i + 1]:
                _put(params, option, _parse_scalar(tokens[i + 1]))
                i += 2
                continue
            _put(params, option, True)
            i += 1
            continue
        if "=" in token:
            key, raw = token.split("=", 1)
            _put(params, key, _parse_scalar(raw))
            i += 1
            continue
        payload = _load_json_source(token) if token.startswith("@") or token == "-" else _parse_scalar(token)
        if isinstance(payload, dict):
            _merge(params, payload)
            i += 1
            continue
        raise CliUsageError(f"unrecognized parameter {token!r}; use key=value, --key value, or --input")
    return params


def _extract(value: Any, path: Optional[str]) -> Any:
    if path in (None, "", "."):
        return value
    cursor = value
    for part in path.strip(".").split("."):
        try:
            cursor = cursor[int(part)] if isinstance(cursor, list) else cursor[part]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise CliUsageError(f"output path not found: {path}") from exc
    return cursor


def _shell_key(parts: List[str]) -> str:
    raw = "_".join(parts) if parts else "RESULT"
    key = re.sub(r"[^A-Za-z0-9_]", "_", raw).upper().strip("_") or "RESULT"
    return f"_{key}" if key[0].isdigit() else key


def _shell_lines(value: Any, parts: Optional[List[str]] = None) -> List[str]:
    parts = parts or []
    if isinstance(value, dict):
        lines: List[str] = []
        for key, item in value.items():
            if isinstance(item, dict):
                lines.extend(_shell_lines(item, [*parts, str(key)]))
            else:
                encoded = json.dumps(item, ensure_ascii=False, separators=(",", ":")) if isinstance(item, (list, dict)) else ("" if item is None else str(item))
                lines.append(f"{_shell_key([*parts, str(key)])}={shlex.quote(encoded)}")
        return lines
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")) if isinstance(value, (list, dict)) else ("" if value is None else str(value))
    return [f"{_shell_key(parts)}={shlex.quote(encoded)}"]


def emit(value: Any, *, output: str, pretty: bool, raw_path: Optional[str]) -> None:
    normalized = normalize(value)
    selected = _extract(normalized, raw_path) if raw_path is not None else normalized
    if output == "raw":
        if selected is None:
            return
        if isinstance(selected, (dict, list)):
            sys.stdout.write(json.dumps(selected, ensure_ascii=False, separators=(",", ":")) + "\n")
        elif isinstance(selected, bool):
            sys.stdout.write(("true" if selected else "false") + "\n")
        else:
            sys.stdout.write(str(selected) + "\n")
    elif output == "shell":
        lines = _shell_lines(selected)
        if lines:
            sys.stdout.write("\n".join(lines) + "\n")
    elif output == "jsonl":
        sys.stdout.write(json.dumps(selected, ensure_ascii=False, separators=(",", ":")) + "\n")
    else:
        indent = 2 if pretty else None
        separators = None if pretty else (",", ":")
        sys.stdout.write(json.dumps(selected, ensure_ascii=False, indent=indent, separators=separators) + "\n")
    sys.stdout.flush()


def result_is_error(value: Any) -> bool:
    value = normalize(value)
    if not isinstance(value, dict):
        return False
    if value.get("success") is False or value.get("isError") is True:
        return True
    if value.get("status") == "confirmation_required":
        return True
    error = value.get("error")
    return error not in (None, False, "", {})


def _global_options(argv: List[str]) -> tuple[List[str], Dict[str, Any]]:
    opts: Dict[str, Any] = {
        "surface": "compound",
        "output": "json",
        "pretty": None,
        "raw_path": None,
        "inputs": [],
        "sets": [],
        "yes": False,
    }
    rest: List[str] = []
    i = 0
    valued = {"--surface": "surface", "--output": "output", "-o": "output", "--raw": "raw_path", "--input": "inputs", "-i": "inputs", "--set": "sets", "-s": "sets"}
    while i < len(argv):
        token = argv[i]
        key = valued.get(token)
        if key:
            if i + 1 >= len(argv):
                raise CliUsageError(f"{token} requires a value")
            if key in ("inputs", "sets"):
                opts[key].append(argv[i + 1])
            else:
                opts[key] = argv[i + 1]
            i += 2
            continue
        matched = False
        for option, option_key in (("--surface=", "surface"), ("--output=", "output"), ("--raw=", "raw_path"), ("--input=", "inputs"), ("--set=", "sets")):
            if token.startswith(option):
                value = token[len(option):]
                opts[option_key].append(value) if option_key in ("inputs", "sets") else opts.__setitem__(option_key, value)
                matched = True
                break
        if matched:
            i += 1
            continue
        if token == "--pretty":
            opts["pretty"] = True
        elif token == "--compact":
            opts["pretty"] = False
        elif token == "--yes":
            opts["yes"] = True
        else:
            rest.append(token)
        i += 1
    if opts["surface"] not in (*SURFACES, "all"):
        raise CliUsageError("--surface must be compound, granular, or all")
    if opts["output"] not in OUTPUTS:
        raise CliUsageError(f"--output must be one of: {', '.join(OUTPUTS)}")
    if opts["raw_path"] is not None:
        opts["output"] = "raw"
    if opts["pretty"] is None:
        opts["pretty"] = opts["output"] == "json" and sys.stdout.isatty()
    return rest, opts


def _surfaces(value: str) -> List[str]:
    return list(SURFACES) if value == "all" else [value]


def _prompt_rows(surface: str) -> List[Dict[str, Any]]:
    return [
        {
            "name": name,
            "surface": surface,
            "description": prompt.description,
            "arguments": normalize(prompt.arguments),
        }
        for name, prompt in sorted(build_prompt_registry(surface).items())
    ]


def _resource_rows(surface: str) -> List[Dict[str, Any]]:
    concrete = [
        {
            "uri": str(uri),
            "surface": surface,
            "kind": "resource",
            "name": resource.name,
            "description": resource.description,
            "mime_type": resource.mime_type,
        }
        for uri, resource in sorted(build_resource_registry(surface).items(), key=lambda item: str(item[0]))
    ]
    templates = [
        {
            "uri": str(uri),
            "surface": surface,
            "kind": "template",
            "name": resource.name,
            "description": resource.description,
            "mime_type": resource.mime_type,
        }
        for uri, resource in sorted(build_resource_template_registry(surface).items(), key=lambda item: str(item[0]))
    ]
    return concrete + templates


def _usage() -> str:
    return f"""DaVinci Resolve CLI {VERSION} — the complete MCP surface for Bash

Usage:
  dvr tools [--surface compound|granular|all]
  dvr describe TOOL [ACTION] [--surface compound|granular]
  dvr actions TOOL
  dvr call TOOL [ACTION] [PARAM ...]
  dvr TOOL ACTION [PARAM ...]              compound shortcut
  dvr granular TOOL [PARAM ...]            direct granular tool
  dvr prompts | prompt NAME [PARAM ...]
  dvr resources | resource URI
  dvr completion bash|zsh|fish
  dvr session                              persistent JSONL request loop

Parameters:
  key=value              JSON scalars/objects/arrays are decoded
  --key value            dynamic named parameter (`--flag` means true)
  -i, --input JSON|@FILE|-  merge a JSON object ("-" reads stdin)
  -s, --set KEY=VALUE    set a dotted/nested key; repeat as needed
  --yes                  replay one confirmation-token response explicitly

Output (stdout contains data only):
  -o, --output json|jsonl|raw|shell
  --pretty | --compact
  --raw PATH             dot path such as jobs.0.id (implies raw output)

Session JSONL (one response envelope per request; registries/connection stay warm):
  {{"id":1,"tool":"timeline","action":"get_current","params":{{}}}}
  {{"id":2,"argv":["actions","timeline"]}}
  {{"id":3,"quit":true}}

Management/offline commands are provided by the launcher:
  dvr advanced TOOL ACTION ...
  dvr batch ... | production ... | setup ... | doctor ... | server ... | control-panel ...

Exit codes: 0 success, 1 tool error/refusal, 2 usage/input, 3 internal, 130 interrupted
"""


_TOP_LEVEL_COMMANDS = (
    "tools", "describe", "actions", "call", "granular", "prompts", "prompt",
    "resources", "resource", "completion", "session", "advanced", "batch", "production",
    "setup", "doctor", "server", "control-panel", "help", "version",
)
_COMMON_PARAMETER_FLAGS = (
    "--input", "--set", "--output", "--raw", "--pretty", "--compact", "--yes",
)


def _schema_flags(schema: Any) -> List[str]:
    if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
        return []
    return [f"--{str(name).replace('_', '-')}" for name in schema["properties"]]


def _action_parameter_flags(tool_name: str, action: str) -> List[str]:
    tool = build_registry("compound").get(tool_name)
    if tool is None or action not in discover_actions(tool_name):
        return []
    metadata = describe_action(tool, "compound", action)
    return [
        f"--{row['name'].replace('_', '-')}"
        for row in metadata.get("parameters", [])
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    ]


def _enum_candidates(words: List[str]) -> Optional[List[str]]:
    if len(words) < 2:
        return None
    generic = {
        "--surface": ["compound", "granular", "all"],
        "--output": list(OUTPUTS),
        "-o": list(OUTPUTS),
        "--track-type": ["video", "audio", "subtitle"],
        "--track_type": ["video", "audio", "subtitle"],
    }.get(words[-2])
    if generic is not None:
        return generic
    flag = words[-2]
    if flag == "--preset" and "edit_engine" in words and any(
        action in words for action in (
            "animated_caption_presets", "plan_animated_captions",
            "create_animated_captions", "plan_caption_delivery",
        )
    ):
        return ["clean", "pop", "word-highlight", "karaoke"]
    if flag == "--duration-preset" and "advanced" in words and "drp" in words:
        return ["subtle", "standard", "slow"]
    if flag == "--entrance" and "advanced" in words and "fusion" in words:
        return ["none", "fade", "pop", "punch"]
    return None


def completion_candidates(words: List[str]) -> List[str]:
    """Return candidates for a command line excluding the executable.

    The current, possibly empty, token is the last item. Keeping registry and
    action introspection here makes the three shell adapters small and gives
    new MCP tools/actions completion automatically.
    """
    words = list(words) or [""]
    prefix = words[-1]
    enum_values = _enum_candidates(words)
    if enum_values is not None:
        return sorted(value for value in enum_values if value.startswith(prefix))

    complete_raw = words[:-1]
    complete: List[str] = []
    value_options = {"--surface", "--output", "-o", "--raw", "--input", "-i", "--set", "-s"}
    flag_options = {"--pretty", "--compact", "--yes"}
    index = 0
    while index < len(complete_raw):
        token = complete_raw[index]
        if token in value_options:
            index += 2
            continue
        if token in flag_options or any(token.startswith(f"{option}=") for option in value_options):
            index += 1
            continue
        complete.append(token)
        index += 1
    compound: Optional[List[str]] = None

    def compound_names() -> List[str]:
        nonlocal compound
        if compound is None:
            compound = sorted(build_registry("compound"))
        return compound

    candidates: List[str]
    if not complete:
        candidates = [*_TOP_LEVEL_COMMANDS, *compound_names(), "--surface", "--output"]
    else:
        command = complete[0]
        if command in ("describe", "actions", "call"):
            if len(complete) == 1:
                candidates = compound_names()
            elif command in ("describe", "call") and len(complete) == 2:
                candidates = discover_actions(complete[1])
            elif command == "call" and len(complete) >= 3:
                candidates = [*_COMMON_PARAMETER_FLAGS, *_action_parameter_flags(complete[1], complete[2])]
            else:
                candidates = ["--surface", "--output", "--pretty", "--compact"]
        elif command == "granular":
            granular = build_registry("granular")
            if len(complete) == 1:
                candidates = sorted(granular)
            else:
                tool = granular.get(complete[1])
                candidates = [*_COMMON_PARAMETER_FLAGS, *_schema_flags(getattr(tool, "parameters", None))]
        elif command == "completion":
            candidates = ["bash", "zsh", "fish"]
        elif command == "production":
            if len(complete) == 1:
                candidates = [
                    "doctor", "setup", "inspect", "init", "extract-track", "transcribe", "words", "correct", "chunk",
                    "research", "music", "broll", "plan", "apply-a-roll", "attach-asset", "remotion", "import-broll",
                ]
            elif len(complete) == 2 and complete[1] == "music":
                candidates = ["search", "select"]
            elif len(complete) == 2 and complete[1] == "broll":
                candidates = [
                    "context", "ideate", "select", "source-plan", "source-apply",
                    "asset-record", "asset-review", "publish-remotion",
                ]
            else:
                candidates = list(_COMMON_PARAMETER_FLAGS)
        elif command == "advanced":
            advanced = _advanced_action_catalog()
            if len(complete) == 1:
                candidates = sorted(advanced)
            elif len(complete) == 2:
                candidates = advanced.get(complete[1], [])
            else:
                candidates = list(_COMMON_PARAMETER_FLAGS)
        elif command in compound_names():
            if len(complete) == 1:
                candidates = discover_actions(command)
            else:
                candidates = [*_COMMON_PARAMETER_FLAGS, *_action_parameter_flags(command, complete[1])]
        else:
            candidates = [*_COMMON_PARAMETER_FLAGS, "--surface"]
    return sorted(set(candidate for candidate in candidates if candidate.startswith(prefix)))


def _completion(shell: str) -> str:
    if shell == "bash":
        return """_dvr_complete() {
  local -a args
  local IFS=$'\\n'
  args=("${COMP_WORDS[@]:1:$COMP_CWORD}")
  COMPREPLY=( $(command dvr __complete "${args[@]}" 2>/dev/null) )
}
complete -F _dvr_complete dvr davinci-resolve davinci-resolve-cli
"""
    if shell == "zsh":
        return """#compdef dvr davinci-resolve davinci-resolve-cli
_dvr_complete() {
  local -a candidates
  candidates=("${(@f)$(command dvr __complete "${words[@]:1}" 2>/dev/null)}")
  _describe 'dvr value' candidates
}
compdef _dvr_complete dvr davinci-resolve davinci-resolve-cli
"""
    if shell == "fish":
        return """function __dvr_complete
  command dvr __complete (commandline -opc | string split ' ' | tail -n +2) (commandline -ct) 2>/dev/null
end
complete -c dvr -f -a '(__dvr_complete)'
complete -c davinci-resolve -f -a '(__dvr_complete)'
complete -c davinci-resolve-cli -f -a '(__dvr_complete)'
"""
    raise CliUsageError("completion shell must be bash, zsh, or fish")


def _session_argv(request: Dict[str, Any], base_opts: Dict[str, Any]) -> List[str]:
    if "argv" in request:
        argv = request["argv"]
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise CliUsageError("session request argv must be an array of strings")
        result = list(argv)
    else:
        tool = request.get("tool")
        action = request.get("action")
        params = request.get("params", {})
        if not isinstance(tool, str) or not tool:
            raise CliUsageError("session request requires argv or a string tool")
        if not isinstance(params, dict):
            raise CliUsageError("session request params must be an object")
        surface = request.get("surface", base_opts["surface"])
        if surface not in ("compound", "granular"):
            raise CliUsageError("session request surface must be compound or granular")
        if surface == "compound":
            if not isinstance(action, str) or not action:
                raise CliUsageError("compound session request requires a string action")
            result = ["--surface", surface, tool, action]
        else:
            if action not in (None, ""):
                raise CliUsageError("granular session requests are direct tools and do not take action")
            result = ["--surface", surface, "granular", tool]
        if params:
            result.extend(["--input", json.dumps(params, ensure_ascii=False, separators=(",", ":"))])
        if request.get("yes") is True:
            result.append("--yes")

    # A nested session would contend for the same stream. Reading '-' as a JSON
    # parameter would consume all subsequent protocol lines. Refuse both while
    # leaving ordinary one-shot CLI behavior unchanged.
    request_rest, _request_opts = _global_options(result)
    if request_rest and request_rest[0] == "session":
        raise CliUsageError("a session request cannot start another session")
    if "-" in result or any(
        result[index] in ("--input", "-i") and index + 1 < len(result) and result[index + 1] == "-"
        for index in range(len(result))
    ):
        raise CliUsageError("session requests cannot consume the protocol stream as --input -")

    # Inherit startup defaults. Per-request global options, if present later in
    # argv, override these because _global_options processes left-to-right.
    prefix = ["--surface", base_opts["surface"], "--output", "jsonl"]
    if base_opts.get("yes"):
        prefix.append("--yes")
    return [*prefix, *result]


async def run_jsonl_session(
    input_stream: TextIO,
    output_stream: TextIO,
    base_opts: Dict[str, Any],
) -> None:
    """Serve one independent CLI request per JSON line until EOF.

    Imports, FastMCP registries, Resolve proxies, and the asyncio loop stay alive
    across requests. Every nonblank input line gets exactly one envelope so a
    shell coprocess can correlate responses by ``id`` and continue after errors.
    """
    from pydantic import ValidationError

    for line_number, line in enumerate(input_stream, 1):
        if not line.strip():
            continue
        request_id: Any = None
        stop = False
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise CliUsageError("session request must be a JSON object")
            request_id = request.get("id")
            if request.get("quit") is True:
                payload = {
                    "id": request_id,
                    "ok": True,
                    "exit_code": EXIT_OK,
                    "result": {"stopped": True},
                }
                stop = True
            else:
                argv = _session_argv(request, base_opts)
                rest, opts = _global_options(argv)
                result = await _dispatch(rest, opts)
                failed = result_is_error(result)
                normalized_result = normalize(result)
                if opts.get("raw_path") is not None:
                    normalized_result = _extract(normalized_result, opts["raw_path"])
                payload = {
                    "id": request_id,
                    "ok": not failed,
                    "exit_code": EXIT_TOOL_ERROR if failed else EXIT_OK,
                    "result": normalized_result,
                }
        except (CliUsageError, ValidationError, json.JSONDecodeError) as exc:
            payload = {
                "id": request_id,
                "ok": False,
                "exit_code": EXIT_USAGE,
                "error": {"type": type(exc).__name__, "message": str(exc), "line": line_number},
            }
        except Exception as exc:
            payload = {
                "id": request_id,
                "ok": False,
                "exit_code": EXIT_INTERNAL,
                "error": {"type": type(exc).__name__, "message": str(exc), "line": line_number},
            }
        output_stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        output_stream.flush()
        if stop:
            return


async def _dispatch(rest: List[str], opts: Dict[str, Any]) -> Any:
    if not rest or rest[0] in ("help", "--help", "-h"):
        return {"__help__": _usage()}
    command, tail = rest[0], rest[1:]
    if command in ("version", "--version", "-v"):
        return VERSION
    if command == "__complete":
        return {"__candidates__": completion_candidates(tail)}
    if command == "session":
        if tail:
            raise CliUsageError("session takes no positional arguments; send JSONL requests on stdin")
        await run_jsonl_session(sys.stdin, sys.stdout, opts)
        return {"__session__": True}
    if command == "tools":
        rows = [row for surface in _surfaces(opts["surface"]) for row in list_tools(surface)]
        return {"tools": rows, "count": len(rows)}
    if command == "describe":
        if not tail:
            raise CliUsageError("describe requires TOOL")
        surface = opts["surface"]
        if surface == "all":
            matches = [(candidate, build_registry(candidate).get(tail[0])) for candidate in SURFACES]
            matches = [(candidate, tool) for candidate, tool in matches if tool]
            if len(matches) != 1:
                raise CliUsageError(f"describe {tail[0]!r} is missing or ambiguous; choose --surface")
            surface, tool = matches[0]
        else:
            tool = build_registry(surface).get(tail[0])
        if tool is None:
            raise CliUsageError(f"unknown {surface} tool: {tail[0]}")
        if len(tail) > 2:
            raise CliUsageError("describe accepts TOOL and optional ACTION")
        if len(tail) == 2:
            return {
                "name": tool.name,
                "surface": surface,
                "action": describe_action(tool, surface, tail[1]),
                "tool_input_schema": tool.parameters,
            }
        return _description(tool, surface)
    if command == "actions":
        if not tail:
            raise CliUsageError("actions requires a compound TOOL")
        if tail[0] not in build_registry("compound"):
            raise CliUsageError(f"unknown compound tool: {tail[0]}")
        actions = discover_actions(tail[0])
        return {"tool": tail[0], "actions": actions, "count": len(actions)}
    if command == "prompts":
        rows = [row for surface in _surfaces(opts["surface"]) for row in _prompt_rows(surface)]
        return {"prompts": rows, "count": len(rows)}
    if command == "prompt":
        if not tail:
            raise CliUsageError("prompt requires NAME")
        surface = "compound" if opts["surface"] == "all" else opts["surface"]
        args = parse_params(tail[1:], opts["inputs"], opts["sets"])
        return await render_registered_prompt(surface, tail[0], args)
    if command == "resources":
        rows = [row for surface in _surfaces(opts["surface"]) for row in _resource_rows(surface)]
        return {"resources": rows, "count": len(rows)}
    if command == "resource":
        if not tail:
            raise CliUsageError("resource requires URI")
        surface = "compound" if opts["surface"] == "all" else opts["surface"]
        return await read_registered_resource(surface, tail[0])
    if command == "completion":
        if len(tail) != 1:
            raise CliUsageError("completion requires bash, zsh, or fish")
        return {"__completion__": _completion(tail[0])}

    surface = opts["surface"]
    if command == "granular":
        surface = "granular"
        if not tail:
            raise CliUsageError("granular requires TOOL")
        name, param_tokens = tail[0], tail[1:]
        arguments = parse_params(param_tokens, opts["inputs"], opts["sets"])
    else:
        if command == "call":
            if not tail:
                raise CliUsageError("call requires TOOL")
            name, tail = tail[0], tail[1:]
        else:
            name = command
        if surface == "all":
            raise CliUsageError("calls require --surface compound or granular")
        if surface == "compound":
            if not tail:
                raise CliUsageError(f"compound tool {name!r} requires ACTION")
            action, param_tokens = tail[0], tail[1:]
            params = parse_params(param_tokens, opts["inputs"], opts["sets"])
            arguments = {"action": action, "params": params}
        else:
            arguments = parse_params(tail, opts["inputs"], opts["sets"])

    result = await call_registered_tool(surface, name, arguments)
    normalized = normalize(result)
    if opts["yes"] and surface == "compound" and isinstance(normalized, dict) and normalized.get("status") == "confirmation_required" and normalized.get("confirm_token"):
        arguments = dict(arguments)
        params = dict(arguments.get("params") or {})
        params["confirm_token"] = normalized["confirm_token"]
        arguments["params"] = params
        result = await call_registered_tool(surface, name, arguments)
    return result


def main(argv: Optional[List[str]] = None) -> int:
    from pydantic import ValidationError
    from src.utils import actor_identity

    actor_identity.set_instance("cli")
    try:
        raw_argv = list(sys.argv[1:] if argv is None else argv)
        # Completion words are syntax being inspected, not options for this
        # helper invocation. Bypass the normal global-option stripper so a
        # partial line such as ``--surface g`` reaches the candidate engine.
        if raw_argv and raw_argv[0] == "__complete":
            candidates = completion_candidates(raw_argv[1:])
            if candidates:
                sys.stdout.write("\n".join(candidates) + "\n")
            return EXIT_OK
        rest, opts = _global_options(raw_argv)
        result = asyncio.run(_dispatch(rest, opts))
        if isinstance(result, dict) and "__help__" in result:
            sys.stdout.write(result["__help__"])
            return EXIT_OK
        if isinstance(result, dict) and "__completion__" in result:
            sys.stdout.write(result["__completion__"])
            return EXIT_OK
        if isinstance(result, dict) and "__candidates__" in result:
            candidates = result["__candidates__"]
            if candidates:
                sys.stdout.write("\n".join(candidates) + "\n")
            return EXIT_OK
        if isinstance(result, dict) and result.get("__session__") is True:
            return EXIT_OK
        emit(result, output=opts["output"], pretty=opts["pretty"], raw_path=opts["raw_path"])
        return EXIT_TOOL_ERROR if result_is_error(result) else EXIT_OK
    except (CliUsageError, ValidationError) as exc:
        sys.stderr.write(f"dvr: {exc}\n")
        return EXIT_USAGE
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except Exception as exc:
        if os.environ.get("DVR_DEBUG"):
            import traceback

            traceback.print_exc()
        else:
            sys.stderr.write(f"dvr: {type(exc).__name__}: {exc}\n")
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
