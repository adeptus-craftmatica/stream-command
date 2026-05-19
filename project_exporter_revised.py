#!/usr/bin/env python3
"""
Studio Project Exporter Pro + Architecture Intelligence

NEW FEATURES:
• Plugin detection & metadata extraction
• Event bus mapping (emit/subscribe tracking)
• Empty/stub module detection
• Architecture violation detection
• Circular dependency detection
• JSON export mode for tooling integration
• Import health metrics
• Configurable rules engine

ORIGINAL FEATURES:
• Directory tree
• Source files with contents
• File sizes & line counts
• Binary asset listing
• Python module dependency graph
• Project statistics
"""

import os
import ast
import json
import re
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Set, Tuple, Optional
import argparse

# ================================================
# CONFIGURATION
# ================================================

EXCLUDED_DIRS = {
    ".venv", "venv", "env",
    "__pycache__",
    ".git", ".github",
    ".idea", ".vscode",
    "node_modules",
    "dist", "build",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "htmlcov", "coverage",
}

TEXT_EXTENSIONS = {
    ".py", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".md", ".txt", ".rst",
    ".html", ".css", ".scss",
    ".js", ".ts", ".jsx", ".tsx",
    ".sql", ".xml", ".qss",
    ".sh", ".bat",
}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".wav", ".mp3", ".ogg",
    ".ttf", ".otf", ".woff", ".woff2",
}

# Architecture rules
ARCHITECTURE_RULES = {
    "no_plugin_to_plugin": True,  # Plugins can't import other plugins
    "no_core_to_plugin": True,  # Core can't import plugins
    "detect_circular": True,  # Flag circular imports
    "flag_empty_modules": True,  # Flag 0-line .py files
}

# Event patterns to detect
EVENT_PATTERNS = {
    "emit": r'\.emit\(["\']([^"\']+)["\']',
    "subscribe": r'\.subscribe\(["\']([^"\']+)["\']',
    "publish": r'\.publish\(["\']([^"\']+)["\']',
    "on": r'\.on\(["\']([^"\']+)["\']',
}


# ================================================
# UTILITIES
# ================================================

def should_skip_dir(name: str) -> bool:
    return name in EXCLUDED_DIRS


def is_text_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EXTENSIONS


def is_binary_asset(path: Path) -> bool:
    return path.suffix.lower() in BINARY_EXTENSIONS


def read_text(path: Path) -> str:
    encodings = ["utf-8", "latin-1", "cp1252"]
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, PermissionError):
            continue
    return "[unable to decode file]"


def count_lines(text: str) -> int:
    return len(text.splitlines())


# ================================================
# DIRECTORY TREE
# ================================================

def build_tree(directory: Path, prefix: str = "") -> List[str]:
    lines = []
    try:
        items = sorted(directory.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    except PermissionError:
        return lines

    items = [i for i in items if not (i.is_dir() and should_skip_dir(i.name))]

    for index, item in enumerate(items):
        connector = "└── " if index == len(items) - 1 else "├── "
        lines.append(prefix + connector + item.name)

        if item.is_dir():
            extension = "    " if index == len(items) - 1 else "│   "
            lines.extend(build_tree(item, prefix + extension))

    return lines


# ================================================
# FILE SCANNER
# ================================================

def scan_project(root: Path) -> Tuple[List[Path], List[Path]]:
    text_files = []
    binary_files = []

    for current_root, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not should_skip_dir(d)]

        for name in files:
            if name.startswith("project_export"):
                continue

            path = Path(current_root) / name

            if is_text_file(path):
                text_files.append(path)
            elif is_binary_asset(path):
                binary_files.append(path)

    return sorted(text_files), sorted(binary_files)


# ================================================
# PLUGIN DETECTION
# ================================================

class PluginInfo:
    def __init__(self, path: Path, metadata: dict):
        self.path = path
        self.name = metadata.get("name", path.parent.name)
        self.version = metadata.get("version", "unknown")
        self.entry = metadata.get("entry", "plugin.py")
        self.description = metadata.get("description", "")
        self.author = metadata.get("author", "")
        self.dependencies = metadata.get("dependencies", [])


def detect_plugins(root: Path, text_files: List[Path]) -> List[PluginInfo]:
    """Detect plugins by finding plugin.json files"""
    plugins = []

    for file in text_files:
        if file.name == "plugin.json":
            try:
                content = read_text(file)
                metadata = json.loads(content)
                plugins.append(PluginInfo(file, metadata))
            except (json.JSONDecodeError, Exception):
                # Try to create minimal plugin info
                plugins.append(PluginInfo(file, {"name": file.parent.name}))

    return sorted(plugins, key=lambda p: p.name)


# ================================================
# EVENT BUS ANALYSIS
# ================================================

class EventInfo:
    def __init__(self):
        self.emitters: Set[str] = set()
        self.subscribers: Set[str] = set()


def analyze_events(root: Path, text_files: List[Path]) -> Dict[str, EventInfo]:
    """Extract event bus patterns from source files"""
    events: Dict[str, EventInfo] = defaultdict(EventInfo)

    for file in text_files:
        if file.suffix != ".py":
            continue

        rel = file.relative_to(root)
        module = str(rel).replace("\\", ".").replace("/", ".").replace(".py", "")
        content = read_text(file)

        # Find emit patterns
        for pattern_name, pattern in EVENT_PATTERNS.items():
            matches = re.findall(pattern, content)
            for event_name in matches:
                if pattern_name in ("emit", "publish"):
                    events[event_name].emitters.add(module)
                elif pattern_name in ("subscribe", "on"):
                    events[event_name].subscribers.add(module)

    return dict(events)


# ================================================
# DEPENDENCY ANALYSIS
# ================================================

def analyze_dependencies(root: Path, text_files: List[Path]) -> Dict[str, List[str]]:
    """Extract import dependencies from Python files"""
    dependencies = {}

    for file in text_files:
        if file.suffix != ".py":
            continue

        rel = file.relative_to(root)
        module = str(rel).replace("\\", ".").replace("/", ".").replace(".py", "")

        try:
            tree = ast.parse(read_text(file))
        except Exception as e:
            # Make parse failures visible instead of silent
            dependencies[module] = [f"[PARSE ERROR: {type(e).__name__}]"]
            continue

        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)

        dependencies[module] = sorted(set(imports))

    return dependencies


# ================================================
# EMPTY MODULE DETECTION
# ================================================

def detect_empty_modules(root: Path, text_files: List[Path]) -> List[Tuple[str, int]]:
    """Find Python files with very few lines (stubs/placeholders)"""
    empty = []
    threshold = 5  # Lines or fewer = suspicious

    for file in text_files:
        if file.suffix != ".py":
            continue

        content = read_text(file)
        lines = count_lines(content)

        # Filter out docstrings/comments-only
        non_empty_lines = [
            line for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        if len(non_empty_lines) <= threshold:
            rel = file.relative_to(root)
            empty.append((str(rel), lines))

    return empty


# ================================================
# CIRCULAR DEPENDENCY DETECTION
# ================================================

def detect_circular_imports(dependencies: Dict[str, List[str]]) -> List[List[str]]:
    """Find circular import chains"""

    def find_cycles(node: str, path: List[str], visited: Set[str]) -> List[List[str]]:
        if node in path:
            cycle_start = path.index(node)
            return [path[cycle_start:] + [node]]

        if node in visited:
            return []

        visited.add(node)
        cycles = []

        for dep in dependencies.get(node, []):
            # Only check internal modules (those in our dependency graph)
            if dep in dependencies:
                cycles.extend(find_cycles(dep, path + [node], visited))

        return cycles

    all_cycles = []
    visited_global = set()

    for module in dependencies:
        if module not in visited_global:
            cycles = find_cycles(module, [], set())
            for cycle in cycles:
                # Normalize cycle representation
                min_idx = cycle.index(min(cycle[:-1]))
                normalized = cycle[min_idx:-1] + cycle[:min_idx] + [cycle[min_idx]]
                if normalized not in all_cycles:
                    all_cycles.append(normalized)
            visited_global.add(module)

    return all_cycles


# ================================================
# ARCHITECTURE VALIDATION
# ================================================

class ArchitectureViolation:
    def __init__(self, rule: str, violator: str, target: str, message: str):
        self.rule = rule
        self.violator = violator
        self.target = target
        self.message = message


def validate_architecture(
        root: Path,
        dependencies: Dict[str, List[str]],
        plugins: List[PluginInfo]
) -> List[ArchitectureViolation]:
    """Check for architecture rule violations"""
    violations = []

    # Build plugin module set
    plugin_modules = set()
    for plugin in plugins:
        plugin_dir = plugin.path.parent.relative_to(root)
        plugin_prefix = str(plugin_dir).replace("\\", ".").replace("/", ".")
        plugin_modules.add(plugin_prefix)

    # Build core module set (everything not in plugins)
    core_modules = set()
    for module in dependencies:
        is_plugin = any(module.startswith(pm) for pm in plugin_modules)
        if not is_plugin:
            core_modules.add(module)

    # Rule 1: No plugin-to-plugin imports
    if ARCHITECTURE_RULES["no_plugin_to_plugin"]:
        for module, imports in dependencies.items():
            module_plugin = next((pm for pm in plugin_modules if module.startswith(pm)), None)
            if module_plugin:
                for imp in imports:
                    imp_plugin = next((pm for pm in plugin_modules if imp.startswith(pm)), None)
                    if imp_plugin and imp_plugin != module_plugin:
                        violations.append(ArchitectureViolation(
                            "no_plugin_to_plugin",
                            module,
                            imp,
                            f"Plugin '{module_plugin}' imports from plugin '{imp_plugin}'"
                        ))

    # Rule 2: No core-to-plugin imports
    if ARCHITECTURE_RULES["no_core_to_plugin"]:
        for module, imports in dependencies.items():
            if module in core_modules:
                for imp in imports:
                    if any(imp.startswith(pm) for pm in plugin_modules):
                        violations.append(ArchitectureViolation(
                            "no_core_to_plugin",
                            module,
                            imp,
                            f"Core module '{module}' imports from plugin code"
                        ))

    return violations


# ================================================
# PLUGIN BOUNDARY VISUALIZATION
# ================================================

class BoundaryInfo:
    def __init__(self, name: str, is_plugin: bool):
        self.name = name
        self.modules: Set[str] = set()
        self.is_plugin = is_plugin
        self.internal_imports: Set[Tuple[str, str]] = set()  # Within boundary
        self.external_imports: Set[Tuple[str, str]] = set()  # Crossing boundary
        self.incoming_cross: Set[Tuple[str, str]] = set()  # Others → This


def visualize_boundaries(
        root: Path,
        dependencies: Dict[str, List[str]],
        plugins: List[PluginInfo]
) -> Dict[str, BoundaryInfo]:
    """
    Create a boundary map showing architectural zones and cross-boundary imports.
    Returns dict of {boundary_name: BoundaryInfo}
    """
    boundaries: Dict[str, BoundaryInfo] = {}

    # Create plugin boundaries
    for plugin in plugins:
        plugin_dir = plugin.path.parent.relative_to(root)
        plugin_prefix = str(plugin_dir).replace("\\", ".").replace("/", ".")
        boundaries[plugin.name] = BoundaryInfo(plugin.name, is_plugin=True)

    # Create core boundary (everything not in plugins)
    boundaries["core"] = BoundaryInfo("core", is_plugin=False)

    # Assign modules to boundaries
    for module in dependencies.keys():
        assigned = False
        for plugin in plugins:
            plugin_dir = plugin.path.parent.relative_to(root)
            plugin_prefix = str(plugin_dir).replace("\\", ".").replace("/", ".")
            if module.startswith(plugin_prefix):
                boundaries[plugin.name].modules.add(module)
                assigned = True
                break

        if not assigned:
            boundaries["core"].modules.add(module)

    # Analyze imports to categorize as internal vs cross-boundary
    for module, imports in dependencies.items():
        # Find which boundary this module belongs to
        module_boundary = None
        for boundary_name, boundary in boundaries.items():
            if module in boundary.modules:
                module_boundary = boundary_name
                break

        if not module_boundary:
            continue

        for imp in imports:
            # Skip external libraries (not in our dependency graph)
            if imp not in dependencies:
                continue

            # Find which boundary the import belongs to
            imp_boundary = None
            for boundary_name, boundary in boundaries.items():
                if imp in boundary.modules:
                    imp_boundary = boundary_name
                    break

            if not imp_boundary:
                continue

            # Categorize the import
            if module_boundary == imp_boundary:
                # Internal import (within same boundary)
                boundaries[module_boundary].internal_imports.add((module, imp))
            else:
                # Cross-boundary import
                boundaries[module_boundary].external_imports.add((module, imp))
                boundaries[imp_boundary].incoming_cross.add((module, imp))

    return boundaries


# ================================================
# EXPORT FORMATS
# ================================================

def export_json(
        root: Path,
        text_files: List[Path],
        binary_files: List[Path],
        dependencies: Dict[str, List[str]],
        plugins: List[PluginInfo],
        events: Dict[str, EventInfo],
        empty_modules: List[Tuple[str, int]],
        circular_imports: List[List[str]],
        violations: List[ArchitectureViolation],
        boundaries: Dict[str, BoundaryInfo]
) -> dict:
    """Generate structured JSON export"""

    total_lines = sum(count_lines(read_text(f)) for f in text_files if f.suffix == ".py")

    return {
        "metadata": {
            "project": root.name,
            "path": str(root.absolute()),
            "exported": datetime.now().isoformat(),
            "stats": {
                "text_files": len(text_files),
                "binary_files": len(binary_files),
                "total_lines": total_lines,
                "plugins": len(plugins),
                "events": len(events),
            }
        },
        "plugins": [
            {
                "name": p.name,
                "version": p.version,
                "entry": p.entry,
                "description": p.description,
                "author": p.author,
                "path": str(p.path.parent.relative_to(root)),
            }
            for p in plugins
        ],
        "events": {
            name: {
                "emitters": sorted(list(info.emitters)),
                "subscribers": sorted(list(info.subscribers))
            }
            for name, info in events.items()
        },
        "dependencies": dependencies,
        "boundaries": {
            name: {
                "is_plugin": boundary.is_plugin,
                "modules": sorted(list(boundary.modules)),
                "internal_imports": len(boundary.internal_imports),
                "outgoing_imports": len(boundary.external_imports),
                "incoming_imports": len(boundary.incoming_cross),
                "coupling_percentage": (
                                               (len(boundary.external_imports) + len(boundary.incoming_cross))
                                               / max(len(boundary.internal_imports) + len(boundary.external_imports), 1)
                                       ) * 100
            }
            for name, boundary in boundaries.items()
        },
        "issues": {
            "empty_modules": [
                {"file": path, "lines": lines}
                for path, lines in empty_modules
            ],
            "circular_imports": circular_imports,
            "architecture_violations": [
                {
                    "rule": v.rule,
                    "violator": v.violator,
                    "target": v.target,
                    "message": v.message
                }
                for v in violations
            ]
        },
        "files": {
            "text": [str(f.relative_to(root)) for f in text_files],
            "binary": [str(f.relative_to(root)) for f in binary_files]
        }
    }


def export_text(
        root: Path,
        output_path: Path,
        text_files: List[Path],
        binary_files: List[Path],
        dependencies: Dict[str, List[str]],
        plugins: List[PluginInfo],
        events: Dict[str, EventInfo],
        empty_modules: List[Tuple[str, int]],
        circular_imports: List[List[str]],
        violations: List[ArchitectureViolation]
):
    """Generate human-readable text export (enhanced version of original)"""

    tree = [root.name + "/"]
    tree.extend(build_tree(root))
    total_lines = 0

    with open(output_path, "w", encoding="utf-8") as out:

        # Header
        out.write("=" * 80 + "\n")
        out.write("PROJECT EXPORT PRO\n")
        out.write("=" * 80 + "\n")
        out.write(f"Project : {root.name}\n")
        out.write(f"Path    : {root.absolute()}\n")
        out.write(f"Date    : {datetime.now()}\n\n")

        # ================================================
        # STATISTICS
        # ================================================
        out.write("=" * 80 + "\n")
        out.write("PROJECT STATISTICS\n")
        out.write("=" * 80 + "\n")
        out.write(f"Text files    : {len(text_files)}\n")
        out.write(f"Binary assets : {len(binary_files)}\n")
        out.write(f"Plugins found : {len(plugins)}\n")
        out.write(f"Events tracked: {len(events)}\n\n")

        # ================================================
        # PLUGINS
        # ================================================
        if plugins:
            out.write("=" * 80 + "\n")
            out.write("PLUGINS DETECTED\n")
            out.write("=" * 80 + "\n\n")

            for plugin in plugins:
                out.write(f"• {plugin.name}\n")
                out.write(f"  Version     : {plugin.version}\n")
                out.write(f"  Entry point : {plugin.entry}\n")
                if plugin.description:
                    out.write(f"  Description : {plugin.description}\n")
                if plugin.author:
                    out.write(f"  Author      : {plugin.author}\n")
                out.write(f"  Location    : {plugin.path.parent.relative_to(root)}\n")
                if plugin.dependencies:
                    out.write(f"  Dependencies: {', '.join(plugin.dependencies)}\n")
                out.write("\n")

        # ================================================
        # EVENT MAP
        # ================================================
        if events:
            out.write("=" * 80 + "\n")
            out.write("EVENT BUS MAP\n")
            out.write("=" * 80 + "\n\n")

            for event_name in sorted(events.keys()):
                info = events[event_name]
                out.write(f"📡 {event_name}\n")

                if info.emitters:
                    for emitter in sorted(info.emitters):
                        out.write(f"   ├─ emitted by: {emitter}\n")

                if info.subscribers:
                    for subscriber in sorted(info.subscribers):
                        out.write(f"   └─ consumed by: {subscriber}\n")

                if not info.emitters and not info.subscribers:
                    out.write("   (no usage detected)\n")

                out.write("\n")

        # ================================================
        # ARCHITECTURE ISSUES
        # ================================================
        has_issues = empty_modules or circular_imports or violations

        if has_issues:
            out.write("=" * 80 + "\n")
            out.write("ARCHITECTURE ANALYSIS\n")
            out.write("=" * 80 + "\n\n")

            # Empty modules
            if empty_modules:
                out.write("⚠️  EMPTY/STUB MODULES\n")
                out.write("-" * 40 + "\n")
                for path, lines in empty_modules:
                    out.write(f"  • {path} ({lines} lines)\n")
                out.write("\n")

            # Circular imports
            if circular_imports:
                out.write("🔄 CIRCULAR IMPORTS\n")
                out.write("-" * 40 + "\n")
                for cycle in circular_imports:
                    out.write("  • " + " → ".join(cycle) + "\n")
                out.write("\n")

            # Architecture violations
            if violations:
                out.write("❌ ARCHITECTURE VIOLATIONS\n")
                out.write("-" * 40 + "\n")
                for v in violations:
                    out.write(f"  • {v.message}\n")
                    out.write(f"    {v.violator} → {v.target}\n")
                out.write("\n")

        # ================================================
        # PLUGIN BOUNDARIES
        # ================================================
        boundaries = visualize_boundaries(root, dependencies, plugins)

        out.write("=" * 80 + "\n")
        out.write("PLUGIN BOUNDARIES\n")
        out.write("=" * 80 + "\n\n")

        # Sort: core first, then plugins alphabetically
        sorted_boundaries = sorted(
            boundaries.items(),
            key=lambda x: (x[1].is_plugin, x[0])
        )

        for boundary_name, boundary in sorted_boundaries:
            # Boundary header
            icon = "🔌" if boundary.is_plugin else "⚙️"
            out.write(f"{icon} {boundary_name}\n")

            # Show modules in this boundary (grouped by parent)
            module_tree = defaultdict(list)
            for module in sorted(boundary.modules):
                parts = module.split(".")
                if len(parts) == 1:
                    module_tree[""].append(parts[0])
                else:
                    parent = parts[0]
                    child = ".".join(parts[1:])
                    module_tree[parent].append(child)

            # Display tree
            for parent, children in sorted(module_tree.items()):
                if parent == "":
                    for child in children:
                        out.write(f"   └─ {child}\n")
                else:
                    out.write(f"   ├─ {parent}/\n")
                    for idx, child in enumerate(sorted(children)):
                        prefix = "   │  " if idx < len(children) - 1 else "   │  "
                        connector = "├─" if idx < len(children) - 1 else "└─"
                        out.write(f"{prefix}{connector} {child}\n")

            # Show cross-boundary imports
            if boundary.external_imports:
                out.write(f"   \n")
                out.write(f"   ↗️  Outgoing cross-boundary imports:\n")

                # Group by target boundary
                by_target = defaultdict(list)
                for src, dst in boundary.external_imports:
                    target_boundary = None
                    for b_name, b_info in boundaries.items():
                        if dst in b_info.modules:
                            target_boundary = b_name
                            break
                    if target_boundary:
                        by_target[target_boundary].append((src, dst))

                for target, imports in sorted(by_target.items()):
                    out.write(f"      → {target} ({len(imports)} imports)\n")
                    # Show first 3 examples
                    for src, dst in sorted(imports)[:3]:
                        src_short = src.split(".")[-1]
                        dst_short = dst.split(".")[-1]
                        out.write(f"         • {src_short} → {dst_short}\n")
                    if len(imports) > 3:
                        out.write(f"         ... and {len(imports) - 3} more\n")

            out.write("\n")

        # Summary statistics
        out.write("-" * 80 + "\n")
        out.write("BOUNDARY STATISTICS\n")
        out.write("-" * 80 + "\n")

        for boundary_name, boundary in sorted_boundaries:
            internal = len(boundary.internal_imports)
            outgoing = len(boundary.external_imports)
            incoming = len(boundary.incoming_cross)
            total = internal + outgoing

            if total > 0:
                coupling = (outgoing + incoming) / total * 100
            else:
                coupling = 0

            out.write(f"{boundary_name}:\n")
            out.write(f"  Modules   : {len(boundary.modules)}\n")
            out.write(f"  Internal  : {internal} imports (within boundary)\n")
            out.write(f"  Outgoing  : {outgoing} imports (to other boundaries)\n")
            out.write(f"  Incoming  : {incoming} imports (from other boundaries)\n")
            out.write(f"  Coupling  : {coupling:.1f}%\n")
            out.write("\n")

        out.write("\n")

        # ================================================
        # DIRECTORY TREE
        # ================================================
        out.write("=" * 80 + "\n")
        out.write("DIRECTORY TREE\n")
        out.write("=" * 80 + "\n\n")
        out.write("\n".join(tree))
        out.write("\n\n")

        # ================================================
        # BINARY ASSETS
        # ================================================
        out.write("=" * 80 + "\n")
        out.write("BINARY ASSETS\n")
        out.write("=" * 80 + "\n\n")

        for file in binary_files:
            size_kb = file.stat().st_size / 1024
            rel = file.relative_to(root)
            out.write(f"{rel}  ({size_kb:.1f} KB)\n")

        out.write("\n\n")

        # ================================================
        # PROJECT ARCHITECTURE
        # ================================================
        out.write("=" * 80 + "\n")
        out.write("DEPENDENCY GRAPH\n")
        out.write("=" * 80 + "\n\n")

        for module, imports in dependencies.items():
            out.write(module + "\n")
            for imp in imports:
                out.write(f" └─ {imp}\n")
            out.write("\n")

        # ================================================
        # SOURCE FILES
        # ================================================
        out.write("=" * 80 + "\n")
        out.write("SOURCE FILES\n")
        out.write("=" * 80 + "\n")

        for file in text_files:
            rel = file.relative_to(root)
            content = read_text(file)
            lines = count_lines(content)
            total_lines += lines

            out.write("\n")
            out.write("=" * 80 + "\n")
            out.write(f"FILE: {rel}\n")
            out.write(f"Lines: {lines}\n")
            out.write(f"Size : {file.stat().st_size / 1024:.1f} KB\n")
            out.write("=" * 80 + "\n\n")

            out.write(content)
            if not content.endswith("\n"):
                out.write("\n")

    return total_lines


# ================================================
# MAIN EXPORT
# ================================================

def export_project(
        root: Path,
        output_file: Optional[str] = None,
        format: str = "text"
) -> Path:
    """
    Main export function with enhanced intelligence

    Args:
        root: Project root directory
        output_file: Output filename (auto-generated if None)
        format: 'text' or 'json'
    """

    print("🔍 Scanning project...")
    text_files, binary_files = scan_project(root)

    print("🔌 Detecting plugins...")
    plugins = detect_plugins(root, text_files)

    print("📡 Mapping event bus...")
    events = analyze_events(root, text_files)

    print("🔗 Analyzing dependencies...")
    dependencies = analyze_dependencies(root, text_files)

    print("⚠️  Checking for empty modules...")
    empty_modules = detect_empty_modules(root, text_files)

    print("🔄 Detecting circular imports...")
    circular_imports = detect_circular_imports(dependencies)

    print("🏗️  Validating architecture...")
    violations = validate_architecture(root, dependencies, plugins)

    print("🗺️  Mapping boundaries...")
    boundaries = visualize_boundaries(root, dependencies, plugins)

    # Generate output filename
    if output_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = "json" if format == "json" else "txt"
        output_file = f"project_export_{timestamp}.{ext}"

    output_path = root / output_file

    print(f"📝 Writing {format.upper()} export...")

    if format == "json":
        data = export_json(
            root, text_files, binary_files, dependencies,
            plugins, events, empty_modules, circular_imports, violations, boundaries
        )
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        total_lines = data["metadata"]["stats"]["total_lines"]
    else:
        total_lines = export_text(
            root, output_path, text_files, binary_files, dependencies,
            plugins, events, empty_modules, circular_imports, violations
        )

    # Summary
    print("\n" + "=" * 50)
    print("✅ Export complete!")
    print("=" * 50)
    print(f"Text files : {len(text_files)}")
    print(f"Binary     : {len(binary_files)}")
    print(f"Total lines: {total_lines}")
    print(f"Plugins    : {len(plugins)}")
    print(f"Events     : {len(events)}")

    if empty_modules:
        print(f"⚠️  Empty    : {len(empty_modules)} stub modules")
    if circular_imports:
        print(f"🔄 Circular : {len(circular_imports)} import cycles")
    if violations:
        print(f"❌ Violations: {len(violations)} architecture issues")

    print(f"\n📄 Output: {output_path}")

    return output_path


# ================================================
# CLI
# ================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Project Exporter Pro - Architecture Intelligence System"
    )

    parser.add_argument(
        "--root",
        default=".",
        help="Project root directory (default: current directory)"
    )

    parser.add_argument(
        "--output",
        help="Output filename (auto-generated if not specified)"
    )

    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Export format: text (human-readable) or json (structured data)"
    )

    args = parser.parse_args()

    export_project(Path(args.root), args.output, args.format)