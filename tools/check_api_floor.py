#!/usr/bin/env python3
"""
Report GTK / libadwaita API newer than the oldest board can load.

Pardus ETAP 23.4 is Debian 12, so the floor is GTK 4.8 and libadwaita 1.2
(docs/packaging.md). The GIR files record the version each symbol appeared in;
this walks the application's Python source and compares.

    python3 tools/check_api_floor.py --maybe    # exit 1 if anything is too new

Class, constructor-property and enum use is resolved exactly. A method call on
an arbitrary receiver cannot be typed without running the code, so it is only
reported when every GIR method of that name is newer than the floor. A line
that ends in `# floor: ok` is a guarded use of newer API and is skipped.
"""

import argparse
import ast
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GIR_DIR = "/usr/share/gir-1.0"
CORE = "{http://www.gtk.org/introspection/core/1.0}"

# namespace -> (GIR file, floor)
FLOORS = {
    "Gtk": ("Gtk-4.0.gir", (4, 8)),
    "Gdk": ("Gdk-4.0.gir", (4, 8)),
    "Gsk": ("Gsk-4.0.gir", (4, 8)),
    "Adw": ("Adw-1.gir", (1, 2)),
    "Pango": ("Pango-1.0.gir", (1, 50)),
    "Gio": ("Gio-2.0.gir", (2, 74)),
    "GObject": ("GObject-2.0.gir", (2, 74)),
}

SOURCE_DIRS = ("interaktiv_gtk",)

WAIVER = "# floor: ok"

# Method names shared with Python builtins and containers: a `.get()` on a
# dict is not `Gtk.Something.get()`.
IGNORED_METHODS = {"get", "set", "append", "remove", "insert", "clear", "copy",
                   "update", "add", "pop", "index", "count", "sort", "start",
                   "stop", "close", "open", "read", "write", "run", "join"}


def parse_version(text):
    if not text:
        return None
    try:
        return tuple(int(part) for part in text.split(".")[:2])
    except ValueError:
        return None


class Namespace:
    def __init__(self, name, path, floor):
        self.name = name
        self.floor = floor
        self.types = {}                       # type name -> version
        self.members = defaultdict(dict)      # type name -> member name -> version
        self.methods = defaultdict(list)      # method name -> [version or None]
        self._load(path)

    def _load(self, path):
        namespace = ET.parse(path).getroot().find(f"{CORE}namespace")
        for node in namespace:
            kind = node.tag.replace(CORE, "")
            name = node.get("name")
            if not name:
                continue
            if kind in ("class", "interface", "record", "enumeration", "bitfield"):
                version = parse_version(node.get("version"))
                self.types[name] = version
                for child in node:
                    child_kind = child.tag.replace(CORE, "")
                    child_name = child.get("name")
                    if not child_name:
                        continue
                    child_version = parse_version(child.get("version")) or version
                    if child_kind in ("method", "constructor", "function", "virtual-method"):
                        self.members[name][child_name] = child_version
                        if child_kind == "method":
                            self.methods[child_name].append(child_version)
                    elif child_kind == "property":
                        self.members[name][child_name.replace("-", "_")] = child_version
                    elif child_kind == "member":
                        self.members[name][child_name.upper()] = child_version
            elif kind in ("function", "constant"):
                self.types[name] = parse_version(node.get("version"))

    def too_new(self, version):
        return version is not None and version > self.floor


def load_namespaces():
    namespaces = {}
    for name, (filename, floor) in FLOORS.items():
        path = os.path.join(GIR_DIR, filename)
        if os.path.isfile(path):
            namespaces[name] = Namespace(name, path, floor)
    return namespaces


def fmt(version):
    return ".".join(str(part) for part in version)


class Visitor(ast.NodeVisitor):
    def __init__(self, namespaces, path, waived=(), own_methods=()):
        self.namespaces = namespaces
        self.path = path
        self.waived = set(waived)
        self.own_methods = set(own_methods)
        self.certain = []
        self.maybe = []

    def visit(self, node):
        if getattr(node, "lineno", None) in self.waived:
            return None
        return super().visit(node)

    def _type_of(self, node):
        """(namespace, type name) if `node` is `Gtk.Something`, else None."""
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id in self.namespaces):
            return self.namespaces[node.value.id], node.attr
        return None

    def visit_Attribute(self, node):
        resolved = self._type_of(node)
        if resolved:
            namespace, name = resolved
            version = namespace.types.get(name)
            if namespace.too_new(version):
                self.certain.append(
                    (node.lineno, f"{namespace.name}.{name} needs {namespace.name} {fmt(version)}"))
        inner = self._type_of(node.value)
        if inner:
            namespace, name = inner
            version = namespace.members.get(name, {}).get(node.attr)
            if namespace.too_new(version):
                self.certain.append(
                    (node.lineno,
                     f"{namespace.name}.{name}.{node.attr} needs {namespace.name} {fmt(version)}"))
        self.generic_visit(node)

    def visit_Call(self, node):
        resolved = self._type_of(node.func)
        if resolved:
            namespace, name = resolved
            for keyword in node.keywords:
                version = namespace.members.get(name, {}).get(keyword.arg or "")
                if namespace.too_new(version):
                    self.certain.append(
                        (node.lineno,
                         f"{namespace.name}.{name}({keyword.arg}=) needs "
                         f"{namespace.name} {fmt(version)}"))
        elif isinstance(node.func, ast.Attribute) and not self._type_of(node.func.value):
            method = node.func.attr
            if method not in IGNORED_METHODS and method not in self.own_methods:
                # Too new only if no namespace offers the name at the floor.
                found = [(namespace, version)
                         for namespace in self.namespaces.values()
                         for version in namespace.methods.get(method, ())]
                if found and all(ns.too_new(version) for ns, version in found):
                    oldest = min(found, key=lambda pair: pair[1])
                    self.maybe.append(
                        (node.lineno,
                         f".{method}() exists only from {oldest[0].name} {fmt(oldest[1])}"))
        self.generic_visit(node)


def source_files(root):
    for source_dir in SOURCE_DIRS:
        for directory, _dirs, files in os.walk(os.path.join(root, source_dir)):
            for filename in sorted(files):
                if filename.endswith(".py"):
                    yield os.path.join(directory, filename)


def scan(namespaces, root=ROOT):
    parsed = []
    own_methods = set()
    for path in source_files(root):
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source, filename=path)
        parsed.append((path, source, tree))
        # A method the application defines itself is not the toolkit's.
        own_methods.update(node.name for node in ast.walk(tree)
                           if isinstance(node, ast.FunctionDef))

    certain, maybe = [], []
    for path, source, tree in parsed:
        waived = [number for number, line in enumerate(source.splitlines(), 1)
                  if line.rstrip().endswith(WAIVER)]
        visitor = Visitor(namespaces, path, waived, own_methods)
        visitor.visit(tree)
        relative = os.path.relpath(path, root)
        certain += [(relative, line, text) for line, text in visitor.certain]
        maybe += [(relative, line, text) for line, text in visitor.maybe]
    return sorted(set(certain)), sorted(set(maybe))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--maybe", action="store_true",
                        help="list the method calls that could not be typed")
    args = parser.parse_args(argv)

    namespaces = load_namespaces()
    if not namespaces:
        print(f"No GIR files in {GIR_DIR}; install libgtk-4-dev and libadwaita-1-dev.",
              file=sys.stderr)
        return 2

    certain, maybe = scan(namespaces)
    for path, line, text in certain:
        print(f"{path}:{line}: {text}")
    if args.maybe:
        for path, line, text in maybe:
            print(f"{path}:{line}: maybe: {text}")
    print(f"{len(certain)} above the floor, {len(maybe)} possibly above it",
          file=sys.stderr)
    return 1 if certain or maybe else 0


if __name__ == "__main__":
    raise SystemExit(main())
