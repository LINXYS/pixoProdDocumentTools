from __future__ import annotations

import sys
from pathlib import Path


SKIP_PREFIXES = (
    "torch==",
    "torchvision==",
    "pywin32==",
    "python-magic-bin==",
    "pyreadline3==",
)


def read_requirements(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" not in text:
            return text
    return data.decode("utf-8", errors="replace").replace("\x00", "")


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: filter_requirements.py INPUT OUTPUT", file=sys.stderr)
        return 2

    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    lines = []
    for line in read_requirements(source).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(SKIP_PREFIXES):
            continue
        lines.append(stripped)

    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
