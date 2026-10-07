"""Sauvegarde à chaud des bases (s4mh + statuts/critères) vers la sortie standard.

    docker compose exec -T api python backup.py > sauvegarde.tar.gz

Utilise l'API de sauvegarde SQLite : copie cohérente même si s4mh écrit en
même temps. Contenu de l'archive : s4mh/vinted.db et data/vtd.db.
"""

from __future__ import annotations

import io
import os
import sqlite3
import sys
import tarfile
import tempfile
from contextlib import closing
from pathlib import Path


def snapshot(src: str, dest: Path, read_only: bool) -> bool:
    if not Path(src).is_file():
        print(f"(ignorée, absente) {src}", file=sys.stderr)
        return False
    uri = f"file:{src}?mode=ro" if read_only else f"file:{src}"
    with closing(sqlite3.connect(uri, uri=True)) as source, closing(sqlite3.connect(dest)) as target:
        source.backup(target)
    return True


def main() -> None:
    files = {
        "s4mh/vinted.db": (os.getenv("S4MH_DB", "/s4mh/vinted.db"), True),
        "data/vtd.db": (os.getenv("VTD_DB", "/data/vtd.db"), False),
    }
    buffer = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, (src, ro) in files.items():
            dest = Path(tmp) / name.replace("/", "_")
            if snapshot(src, dest, ro):
                tar.add(dest, arcname=name)
                print(f"sauvegardée : {name}", file=sys.stderr)
    sys.stdout.buffer.write(buffer.getvalue())


if __name__ == "__main__":
    main()
