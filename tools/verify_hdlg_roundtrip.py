#!/usr/bin/env python3
"""Verify HDLG parse/build and HLZS compress/decompress round-trips."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from hdlg_codec import (
    build_hdgl,
    parse_hdgl,
    read_hdlg,
    read_hlzs,
    table_texts,
    write_hlzs,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify .hdlg round-trip behavior.")
    parser.add_argument("input_hdlg", help="source .hdlg file")
    parser.add_argument(
        "--output",
        help="optional rebuilt .hdlg path to keep after verification",
    )
    return parser.parse_args()


def verify(source: Path, output: Path | None) -> None:
    original_hdgl, hlzs_version, table = read_hdlg(source)
    rebuilt_hdgl = build_hdgl(table_texts(table), table.version)

    print(f"source: {source}")
    print(f"entries: {table.count}")
    print(f"HLZS version: 0x{hlzs_version:08X}")
    print(f"HDGL version: 0x{table.version:08X}")
    print(f"HDGL size: {len(original_hdgl)} bytes")
    print(f"trailing padding: {table.trailing_size} bytes")

    if original_hdgl != rebuilt_hdgl:
        original_table = parse_hdgl(original_hdgl)
        rebuilt_table = parse_hdgl(rebuilt_hdgl)
        text_changed = [
            entry.index
            for entry, rebuilt_entry in zip(
                original_table.entries, rebuilt_table.entries, strict=True
            )
            if entry.text != rebuilt_entry.text
        ]
        raise SystemExit(
            "parse/build HDGL mismatch "
            f"(text differences: {text_changed[:20] or 'none'})"
        )
    print("parse/build HDGL: OK")

    if output:
        write_hlzs(rebuilt_hdgl, output, hlzs_version)
        rebuilt_hlzs_path = output
    else:
        temp = tempfile.NamedTemporaryFile(suffix=".hdlg", delete=False)
        rebuilt_hlzs_path = Path(temp.name)
        temp.close()
        write_hlzs(rebuilt_hdgl, rebuilt_hlzs_path, hlzs_version)

    try:
        decompressed_again, _ = read_hlzs(rebuilt_hlzs_path)
        if decompressed_again != rebuilt_hdgl:
            raise SystemExit("HLZS compress/decompress mismatch")
        print("HLZS compress/decompress: OK")
        if output:
            print(f"rebuilt output: {output}")
    finally:
        if not output:
            rebuilt_hlzs_path.unlink(missing_ok=True)


def main() -> None:
    args = parse_args()
    verify(Path(args.input_hdlg), Path(args.output) if args.output else None)


if __name__ == "__main__":
    main()

