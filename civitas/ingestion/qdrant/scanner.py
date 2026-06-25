"""
civitas.ingestion.qdrant.scanner
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Scanner récursif pour la découverte de fichiers à ingérer.

Fonctionnalités:
  · Traversal récursif illimité (N niveaux de sous-dossiers)
  · Filtrage par extension, pattern, taille
  · Affichage de l'arborescence complète découverte
  · Support de fichiers sans extension (Dockerfile, Jenkinsfile...)
  · Résolution des symlinks configurable
  · Stats de découverte détaillées
"""

from __future__ import annotations

import fnmatch
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
#  DISCOVERED FILE
# ─────────────────────────────────────────────────────────────

@dataclass
class DiscoveredFile:
    """Fichier découvert lors d'un scan."""
    path: Path
    relative_path: str       # Relatif au répertoire racine du scan
    extension: str           # Extension (avec le point, ou '' si aucune)
    size_bytes: int
    mtime: float
    depth: int               # Profondeur dans l'arborescence (0 = racine)

    @property
    def size_kb(self) -> float:
        return self.size_bytes / 1024

    @property
    def filename(self) -> str:
        return self.path.name

    def __repr__(self) -> str:
        return f"DiscoveredFile({self.relative_path}, {self.size_bytes}B)"


@dataclass
class ScanResult:
    """Résultat d'un scan de répertoire."""
    root_path: str
    discovered: list[DiscoveredFile] = field(default_factory=list)
    skipped_size: list[str] = field(default_factory=list)      # Trop gros
    skipped_extension: list[str] = field(default_factory=list)  # Extension non autorisée
    skipped_pattern: list[str] = field(default_factory=list)    # Pattern exclu
    errors: list[str] = field(default_factory=list)

    @property
    def total_discovered(self) -> int:
        return len(self.discovered)

    @property
    def total_skipped(self) -> int:
        return len(self.skipped_size) + len(self.skipped_extension) + len(self.skipped_pattern)

    @property
    def total_bytes(self) -> int:
        return sum(f.size_bytes for f in self.discovered)

    @property
    def extensions_found(self) -> dict[str, int]:
        """Compte de fichiers par extension."""
        counts: dict[str, int] = {}
        for f in self.discovered:
            ext = f.extension or "(no extension)"
            counts[ext] = counts.get(ext, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    @property
    def depth_distribution(self) -> dict[int, int]:
        """Distribution par profondeur."""
        dist: dict[int, int] = {}
        for f in self.discovered:
            dist[f.depth] = dist.get(f.depth, 0) + 1
        return dict(sorted(dist.items()))


# ─────────────────────────────────────────────────────────────
#  SCANNER
# ─────────────────────────────────────────────────────────────

# Noms de fichiers sans extension reconnus (IaC/DevOps)
KNOWN_EXTENSIONLESS_FILES = {
    "Dockerfile", "Jenkinsfile", "Makefile", "Vagrantfile",
    "Procfile", "Brewfile", "Guardfile", "Gemfile", "Rakefile",
    ".env", ".htaccess", ".gitignore", ".dockerignore",
}


class FileScanner:
    """
    Scanner récursif de fichiers.

    Supporte une profondeur illimitée et un filtrage flexible.
    Thread-safe (pas d'état mutable partagé).

    Usage simple:
        scanner = FileScanner(
            allowed_extensions=[".yml", ".yaml", ".tf", ".json"],
            max_file_size_mb=50,
        )
        result = scanner.scan("/data/documents/ansible")
        for f in result.discovered:
            print(f.relative_path)

    Usage avancé (générateur):
        for file in scanner.stream("/data/documents"):
            process(file)
    """

    def __init__(
        self,
        allowed_extensions: Optional[list[str]] = None,
        excluded_patterns: Optional[list[str]] = None,
        max_file_size_mb: float = 100.0,
        recursive: bool = True,
        follow_symlinks: bool = False,
        min_file_size_bytes: int = 1,         # Ignorer les fichiers vides
    ) -> None:
        self.allowed_extensions = set(
            ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            for ext in (allowed_extensions or [])
        )
        self.excluded_patterns = excluded_patterns or [
            "*.tmp", "*.log", ".DS_Store", "~$*", "Thumbs.db",
            "*.pyc", "__pycache__", ".git", ".svn", "*.swp",
            "node_modules", ".venv", "venv", "*.egg-info",
        ]
        self.max_file_size_bytes = int(max_file_size_mb * 1024 * 1024)
        self.recursive = recursive
        self.follow_symlinks = follow_symlinks
        self.min_file_size_bytes = min_file_size_bytes

    # ── Extension Detection ────────────────────────────────────

    def _get_extension(self, path: Path) -> str:
        """
        Détermine l'extension d'un fichier.
        Gère les fichiers sans extension (Dockerfile, Jenkinsfile...).
        """
        ext = path.suffix.lower()
        if ext:
            return ext
        # Fichiers sans extension reconnus
        if path.name in KNOWN_EXTENSIONLESS_FILES:
            return path.name  # Ex: "Dockerfile", "Jenkinsfile"
        return ""

    def _is_allowed(self, path: Path) -> tuple[bool, str]:
        """
        Vérifie si un fichier est autorisé.
        Returns (allowed, reason_if_rejected).
        """
        name = path.name

        # Patterns exclus
        for pattern in self.excluded_patterns:
            if fnmatch.fnmatch(name, pattern):
                return False, f"excluded_pattern:{pattern}"
            # Aussi tester le nom de dossier parent
            for part in path.parts:
                if fnmatch.fnmatch(part, pattern):
                    return False, f"excluded_pattern:{pattern}"

        # Taille
        try:
            size = path.stat().st_size
        except OSError:
            return False, "stat_error"

        if size < self.min_file_size_bytes:
            return False, "too_small"
        if size > self.max_file_size_bytes:
            return False, f"too_large:{size}"

        # Extension
        if self.allowed_extensions:
            ext = self._get_extension(path)
            if ext and ext not in self.allowed_extensions:
                # Tolérer aussi le nom complet pour les fichiers connus
                if name not in self.allowed_extensions and name not in KNOWN_EXTENSIONLESS_FILES:
                    return False, f"extension_not_allowed:{ext}"

        return True, ""

    # ── Core Scan ─────────────────────────────────────────────

    def stream(
        self,
        root_path: str | Path,
    ) -> Iterator[DiscoveredFile]:
        """
        Générateur de fichiers découverts.
        Traversal récursif illimité via os.walk.
        """
        root = Path(root_path).resolve()

        if root.is_file():
            # Cas fichier unique
            allowed, reason = self._is_allowed(root)
            if allowed:
                stat = root.stat()
                yield DiscoveredFile(
                    path=root,
                    relative_path=root.name,
                    extension=self._get_extension(root),
                    size_bytes=stat.st_size,
                    mtime=stat.st_mtime,
                    depth=0,
                )
            return

        if not root.is_dir():
            logger.error("Path does not exist or is not accessible: %s", root)
            return

        for dirpath, dirnames, filenames in os.walk(
            root,
            followlinks=self.follow_symlinks,
        ):
            current_dir = Path(dirpath)
            depth = len(current_dir.relative_to(root).parts)

            if not self.recursive and depth > 0:
                break

            # Filtrer les sous-dossiers exclus (in-place pour os.walk)
            dirnames[:] = sorted([
                d for d in dirnames
                if not any(fnmatch.fnmatch(d, p) for p in self.excluded_patterns)
            ])

            for filename in sorted(filenames):
                file_path = current_dir / filename
                allowed, reason = self._is_allowed(file_path)

                if not allowed:
                    logger.debug("SKIP %s (%s)", file_path, reason)
                    continue

                try:
                    stat = file_path.stat()
                    yield DiscoveredFile(
                        path=file_path,
                        relative_path=str(file_path.relative_to(root)),
                        extension=self._get_extension(file_path),
                        size_bytes=stat.st_size,
                        mtime=stat.st_mtime,
                        depth=depth,
                    )
                except OSError as e:
                    logger.warning("Cannot read file stats %s: %s", file_path, e)

    def scan(self, root_path: str | Path) -> ScanResult:
        """
        Scanner un répertoire et retourner le résultat complet.
        Collecte les fichiers découverts ET les statistiques de rejet.
        """
        root = Path(root_path).resolve()
        result = ScanResult(root_path=str(root))

        if not root.exists():
            result.errors.append(f"Path does not exist: {root}")
            return result

        if root.is_file():
            for f in self.stream(root):
                result.discovered.append(f)
            return result

        for dirpath, dirnames, filenames in os.walk(
            root,
            followlinks=self.follow_symlinks,
        ):
            current_dir = Path(dirpath)
            depth = len(current_dir.relative_to(root).parts)

            if not self.recursive and depth > 0:
                break

            dirnames[:] = sorted([
                d for d in dirnames
                if not any(fnmatch.fnmatch(d, p) for p in self.excluded_patterns)
            ])

            for filename in sorted(filenames):
                file_path = current_dir / filename
                allowed, reason = self._is_allowed(file_path)

                if not allowed:
                    if "too_large" in reason:
                        result.skipped_size.append(str(file_path))
                    elif "extension_not_allowed" in reason:
                        result.skipped_extension.append(str(file_path))
                    elif "excluded_pattern" in reason:
                        result.skipped_pattern.append(str(file_path))
                    continue

                try:
                    stat = file_path.stat()
                    result.discovered.append(DiscoveredFile(
                        path=file_path,
                        relative_path=str(file_path.relative_to(root)),
                        extension=self._get_extension(file_path),
                        size_bytes=stat.st_size,
                        mtime=stat.st_mtime,
                        depth=depth,
                    ))
                except OSError as e:
                    result.errors.append(f"{file_path}: {e}")

        return result

    def print_tree(
        self,
        root_path: str | Path,
        max_files: int = 200,
    ) -> ScanResult:
        """
        Scanner et afficher l'arborescence complète avec Rich.
        """
        try:
            from rich.tree import Tree
            from rich.console import Console
            from rich import print as rprint
            has_rich = True
        except ImportError:
            has_rich = False

        result = self.scan(root_path)

        if has_rich:
            console = Console()
            root = Path(root_path).resolve()
            tree = Tree(f"📁 [bold cyan]{root}[/]")
            nodes: dict[str, object] = {"": tree}

            shown = 0
            for f in result.discovered:
                if shown >= max_files:
                    tree.add(f"[dim]... and {len(result.discovered) - shown} more files[/]")
                    break

                parts = Path(f.relative_path).parts
                parent_key = ""
                for i, part in enumerate(parts[:-1]):
                    key = "/".join(parts[: i + 1])
                    if key not in nodes:
                        parent_node = nodes[parent_key]
                        nodes[key] = parent_node.add(f"📂 [bold yellow]{part}[/]")
                    parent_key = key

                ext = f.extension
                icon = _ext_icon(ext)
                size_str = f"[dim]{f.size_kb:.1f}KB[/]"
                nodes[parent_key].add(f"{icon} [green]{parts[-1]}[/] {size_str}")
                shown += 1

            console.print(tree)
            console.print(
                f"\n[bold]Total:[/] {result.total_discovered} files "
                f"({result.total_bytes / 1024:.0f}KB), "
                f"{result.total_skipped} skipped"
            )
        else:
            print(f"\nScan: {root_path}")
            for f in result.discovered[:max_files]:
                print(f"  {f.relative_path} ({f.size_bytes}B)")
            print(f"\nTotal: {result.total_discovered} files, {result.total_skipped} skipped")

        return result


def _ext_icon(ext: str) -> str:
    """Icône par extension pour l'affichage arbre."""
    icons = {
        ".pdf": "📄", ".docx": "📝", ".doc": "📝", ".xlsx": "📊",
        ".md": "📋", ".txt": "📃", ".html": "🌐", ".htm": "🌐",
        ".json": "🔧", ".yaml": "⚙️", ".yml": "⚙️",
        ".tf": "🏗️", ".tfvars": "🏗️", ".sh": "⚡", ".conf": "⚙️",
        ".py": "🐍", ".js": "🟨", ".ts": "🔷",
        "Dockerfile": "🐳", "Jenkinsfile": "🔄", "Makefile": "🔨",
    }
    return icons.get(ext, "📄")
