#!/usr/bin/env python3
"""CLI for unpacking and repacking script.heslnk archives."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.heslnk_codec import (
    MANIFEST_NAME,
    HeslnkFormatError,
    archive_manifest,
    read_heslnk,
    repack_heslnk,
    unpack_heslnk,
    verify_heslnk,
    write_manifest,
)


def cmd_inspect(args: argparse.Namespace) -> int:
    raw, archive = read_heslnk(args.archive)
    manifest = archive_manifest(args.archive, raw, archive, include_hashes=False)
    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0

    print(f"archive: {args.archive}")
    print(f"version: 0x{archive.version:08x}")
    print(f"count: {archive.count}")
    print(f"unknown1: 0x{archive.unknown1:08x}")
    print(f"unknown2: 0x{archive.unknown2:08x}")
    print(f"name_offset: 0x{archive.name_offset:x}")
    print(f"data_offset: 0x{archive.data_offset:x}")
    print(f"file_size: 0x{archive.file_size:x}")
    print("entries:")
    for entry in archive.entries[: args.limit]:
        print(
            f"  [{entry.index:03d}] {entry.name}.hese "
            f"hash=0x{entry.name_hash:08x} "
            f"offset=0x{entry.offset:x} "
            f"size={entry.unpacked_size} "
            f"compressed={entry.compressed_size}"
        )
    if archive.count > args.limit:
        print(f"  ... {archive.count - args.limit} more")
    return 0


def cmd_unpack(args: argparse.Namespace) -> int:
    archive = unpack_heslnk(args.archive, args.out_dir, args.manifest)
    manifest_path = Path(args.manifest) if args.manifest else Path(args.out_dir) / MANIFEST_NAME
    print(f"unpacked {archive.count} files -> {args.out_dir}")
    print(f"manifest -> {manifest_path}")
    return 0


def cmd_repack(args: argparse.Namespace) -> int:
    archive = repack_heslnk(
        args.in_dir,
        args.archive,
        manifest_path=args.manifest,
        reuse_from=args.reuse_from,
        allow_no_manifest=args.no_manifest,
    )
    print(f"repacked {archive.count} files -> {args.archive}")
    if args.reuse_from:
        print(f"reused unchanged HLZS blocks from {args.reuse_from}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    errors = verify_heslnk(args.source_dir, args.archive)
    if errors:
        print("verify failed:", file=sys.stderr)
        for error in errors[:50]:
            print(f"  {error}", file=sys.stderr)
        if len(errors) > 50:
            print(f"  ... {len(errors) - 50} more", file=sys.stderr)
        return 1
    print("OK")
    return 0


def cmd_manifest(args: argparse.Namespace) -> int:
    raw, archive = read_heslnk(args.archive)
    manifest = archive_manifest(args.archive, raw, archive, include_hashes=True)
    write_manifest(manifest, args.output)
    print(f"manifest -> {args.output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Unpack, inspect, repack, and verify HESLNK archives."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="show archive metadata")
    inspect_parser.add_argument("archive")
    inspect_parser.add_argument("--json", action="store_true", help="print JSON metadata")
    inspect_parser.add_argument(
        "--limit", type=int, default=20, help="number of entries to print"
    )
    inspect_parser.set_defaults(func=cmd_inspect)

    unpack_parser = subparsers.add_parser("unpack", help="extract HESE files")
    unpack_parser.add_argument("archive")
    unpack_parser.add_argument("out_dir")
    unpack_parser.add_argument(
        "--manifest",
        help=f"manifest path (default: OUT_DIR/{MANIFEST_NAME})",
    )
    unpack_parser.set_defaults(func=cmd_unpack)

    repack_parser = subparsers.add_parser("repack", help="build a HESLNK archive")
    repack_parser.add_argument("in_dir")
    repack_parser.add_argument("archive")
    repack_parser.add_argument(
        "--manifest",
        help=f"manifest path (default: IN_DIR/{MANIFEST_NAME})",
    )
    repack_parser.add_argument(
        "--reuse-from",
        help="copy unchanged original HLZS blocks from this archive for byte-exact roundtrips",
    )
    repack_parser.add_argument(
        "--no-manifest",
        action="store_true",
        help="allow rebuilding from sorted *.hese files when no manifest exists",
    )
    repack_parser.set_defaults(func=cmd_repack)

    verify_parser = subparsers.add_parser(
        "verify", help="compare an archive against an unpacked directory"
    )
    verify_parser.add_argument("source_dir")
    verify_parser.add_argument("archive")
    verify_parser.set_defaults(func=cmd_verify)

    manifest_parser = subparsers.add_parser(
        "manifest", help="write only a manifest for an existing archive"
    )
    manifest_parser.add_argument("archive")
    manifest_parser.add_argument(
        "output", nargs="?", default=MANIFEST_NAME, help="manifest output path"
    )
    manifest_parser.set_defaults(func=cmd_manifest)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (HeslnkFormatError, OSError, KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
