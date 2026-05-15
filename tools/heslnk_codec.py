#!/usr/bin/env python3
"""Read and write HESLNK archives.

The ``.heslnk`` container stores a table of named HESE scripts. Each script is
wrapped in an HLZS block; unpacking returns the decompressed ``.hese`` bytes.
"""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.hdlg_codec import HLZS_MAGIC, HLZS_VERSION, lzss_compress, lzss_decompress


HESL_MAGIC = b"HESL"
HESL_VERSION = 0x00010000
HESL_HEADER_SIZE = 48
HESL_ENTRY_SIZE = 16
HESL_ALIGN = 32
MANIFEST_NAME = ".heslnk_manifest.json"
MANIFEST_FORMAT = "heslnk-manifest-v1"


class HeslnkFormatError(ValueError):
    """Raised when a HESLNK archive is malformed or inconsistent."""


@dataclass(frozen=True)
class HeslnkEntry:
    index: int
    name: str
    name_hash: int
    offset: int
    unpacked_size: int
    hlzs_version: int
    compressed_size: int
    entry_padding: bytes
    block_padding_size: int

    @property
    def block_size(self) -> int:
        return 32 + self.compressed_size


@dataclass(frozen=True)
class HeslnkArchive:
    version: int
    count: int
    unknown1: int
    unknown2: int
    header_padding1: bytes
    header_padding2: bytes
    name_offset: int
    data_offset: int
    entries: tuple[HeslnkEntry, ...]
    file_size: int


def _as_path(path: str | Path) -> Path:
    return path if isinstance(path, Path) else Path(path)


def align(value: int, alignment: int = HESL_ALIGN) -> int:
    return (value + alignment - 1) // alignment * alignment


def name_hash(name: str) -> int:
    return zlib.crc32(name.encode("utf-8")) & 0xFFFFFFFF


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode_name(raw: bytes, index: int) -> str:
    try:
        name = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HeslnkFormatError(f"name {index} is not valid UTF-8: {exc}") from exc
    if not name:
        raise HeslnkFormatError(f"name {index} is empty")
    if "\0" in name:
        raise HeslnkFormatError(f"name {index} contains a NUL byte")
    return name


def _read_names(raw: bytes, offset: int, count: int, data_offset: int) -> list[str]:
    names: list[str] = []
    pos = offset
    for index in range(count):
        if pos >= data_offset:
            raise HeslnkFormatError("name table ended before all names were read")
        end = raw.find(b"\0", pos, data_offset)
        if end == -1:
            raise HeslnkFormatError(f"name {index} is missing a NUL terminator")
        names.append(_decode_name(raw[pos:end], index))
        pos = end + 1

    padding = raw[pos:data_offset]
    if any(padding):
        raise HeslnkFormatError("name table padding contains non-zero bytes")
    return names


def _read_hlzs_header(raw: bytes, offset: int) -> tuple[int, int, int]:
    if offset + 32 > len(raw):
        raise HeslnkFormatError(f"HLZS block at {offset:#x} is truncated")

    magic, version, compressed_size, unpacked_size = struct.unpack_from(
        "<4sIII16x", raw, offset
    )
    if magic != HLZS_MAGIC:
        raise HeslnkFormatError(
            f"expected HLZS block at {offset:#x}, got {magic!r}"
        )
    return version, compressed_size, unpacked_size


def parse_heslnk(raw: bytes) -> HeslnkArchive:
    if len(raw) < HESL_HEADER_SIZE:
        raise HeslnkFormatError("file is too small for a HESL header")

    magic, version, count, unknown1, unknown2 = struct.unpack_from("<4sIIII", raw, 0)
    if magic != HESL_MAGIC:
        raise HeslnkFormatError(f"unexpected HESL magic: {magic!r}")

    header_padding1 = raw[20:32]
    name_offset, data_offset = struct.unpack_from("<II", raw, 32)
    header_padding2 = raw[40:48]

    table_end = HESL_HEADER_SIZE + count * HESL_ENTRY_SIZE
    if name_offset < table_end:
        raise HeslnkFormatError(
            f"name offset {name_offset:#x} overlaps entry table ending at {table_end:#x}"
        )
    if data_offset < name_offset:
        raise HeslnkFormatError("data offset is before name table")
    if data_offset > len(raw):
        raise HeslnkFormatError("data offset points past end of file")
    if data_offset % HESL_ALIGN:
        raise HeslnkFormatError(f"data offset {data_offset:#x} is not 0x20-aligned")
    if any(raw[table_end:name_offset]):
        raise HeslnkFormatError("padding before name table contains non-zero bytes")

    names = _read_names(raw, name_offset, count, data_offset)

    raw_entries: list[tuple[int, int, int, bytes]] = []
    for index in range(count):
        entry_pos = HESL_HEADER_SIZE + index * HESL_ENTRY_SIZE
        stored_hash, offset, unpacked_size = struct.unpack_from("<III", raw, entry_pos)
        entry_padding = raw[entry_pos + 12 : entry_pos + 16]
        raw_entries.append((stored_hash, offset, unpacked_size, entry_padding))

    entries: list[HeslnkEntry] = []
    for index, (stored_hash, offset, unpacked_size, entry_padding) in enumerate(
        raw_entries
    ):
        expected_hash = name_hash(names[index])
        if stored_hash != expected_hash:
            raise HeslnkFormatError(
                f"entry {index} hash mismatch for {names[index]!r}: "
                f"{stored_hash:#010x} != {expected_hash:#010x}"
            )
        if any(entry_padding):
            raise HeslnkFormatError(f"entry {index} padding contains non-zero bytes")
        if offset < data_offset:
            raise HeslnkFormatError(f"entry {index} points before data area")
        if offset % HESL_ALIGN:
            raise HeslnkFormatError(f"entry {index} offset {offset:#x} is not aligned")

        hlzs_version, compressed_size, hlzs_unpacked_size = _read_hlzs_header(
            raw, offset
        )
        if hlzs_unpacked_size != unpacked_size:
            raise HeslnkFormatError(
                f"entry {index} size mismatch: table {unpacked_size}, "
                f"HLZS {hlzs_unpacked_size}"
            )

        block_end = offset + 32 + compressed_size
        next_offset = (
            raw_entries[index + 1][1] if index + 1 < len(raw_entries) else len(raw)
        )
        if block_end > next_offset:
            raise HeslnkFormatError(f"entry {index} HLZS payload overlaps next block")
        block_padding = raw[block_end:next_offset]
        if any(block_padding):
            raise HeslnkFormatError(f"entry {index} block padding contains non-zero bytes")

        entries.append(
            HeslnkEntry(
                index=index,
                name=names[index],
                name_hash=stored_hash,
                offset=offset,
                unpacked_size=unpacked_size,
                hlzs_version=hlzs_version,
                compressed_size=compressed_size,
                entry_padding=entry_padding,
                block_padding_size=len(block_padding),
            )
        )

    return HeslnkArchive(
        version=version,
        count=count,
        unknown1=unknown1,
        unknown2=unknown2,
        header_padding1=header_padding1,
        header_padding2=header_padding2,
        name_offset=name_offset,
        data_offset=data_offset,
        entries=tuple(entries),
        file_size=len(raw),
    )


def read_heslnk(path: str | Path) -> tuple[bytes, HeslnkArchive]:
    raw = _as_path(path).read_bytes()
    return raw, parse_heslnk(raw)


def read_hlzs_block(raw: bytes, entry: HeslnkEntry) -> bytes:
    start = entry.offset
    end = entry.offset + entry.block_size
    if end > len(raw):
        raise HeslnkFormatError(f"entry {entry.index} block is truncated")
    return raw[start:end]


def decompress_entry(raw: bytes, entry: HeslnkEntry) -> bytes:
    block = read_hlzs_block(raw, entry)
    data = lzss_decompress(block[32:], 0)
    if len(data) != entry.unpacked_size:
        raise HeslnkFormatError(
            f"entry {entry.index} decompressed to {len(data)} bytes, "
            f"expected {entry.unpacked_size}"
        )
    return data


def make_hlzs_block(data: bytes, version: int = HLZS_VERSION) -> bytes:
    compressed = lzss_compress(data)
    return (
        struct.pack("<4sIII16x", HLZS_MAGIC, version, len(compressed), len(data))
        + compressed
    )


def _safe_output_path(root: Path, name: str) -> Path:
    candidate = root / f"{name}.hese"
    root_resolved = root.resolve()
    parent_resolved = candidate.parent.resolve()
    if root_resolved != parent_resolved and root_resolved not in parent_resolved.parents:
        raise HeslnkFormatError(f"unsafe archive path: {name!r}")
    return candidate


def archive_manifest(
    archive_path: str | Path,
    raw: bytes,
    archive: HeslnkArchive,
    include_hashes: bool = True,
) -> dict[str, Any]:
    source_path = _as_path(archive_path)
    entries: list[dict[str, Any]] = []
    for entry in archive.entries:
        item: dict[str, Any] = {
            "index": entry.index,
            "name": entry.name,
            "hash": f"{entry.name_hash:08x}",
            "offset": entry.offset,
            "unpacked_size": entry.unpacked_size,
            "hlzs_version": entry.hlzs_version,
            "compressed_size": entry.compressed_size,
            "entry_padding": entry.entry_padding.hex(),
        }
        if include_hashes:
            block = read_hlzs_block(raw, entry)
            data = decompress_entry(raw, entry)
            item["sha256"] = sha256(data)
            item["hlzs_sha256"] = sha256(block)
        entries.append(item)

    return {
        "format": MANIFEST_FORMAT,
        "source": str(source_path),
        "source_size": len(raw),
        "source_sha256": sha256(raw) if include_hashes else None,
        "version": archive.version,
        "count": archive.count,
        "unknown1": archive.unknown1,
        "unknown2": archive.unknown2,
        "header_padding1": archive.header_padding1.hex(),
        "header_padding2": archive.header_padding2.hex(),
        "name_offset": archive.name_offset,
        "data_offset": archive.data_offset,
        "alignment": HESL_ALIGN,
        "entries": entries,
    }


def write_manifest(manifest: dict[str, Any], path: str | Path) -> None:
    _as_path(path).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest = json.loads(_as_path(path).read_text(encoding="utf-8"))
    if manifest.get("format") != MANIFEST_FORMAT:
        raise HeslnkFormatError(f"unsupported manifest format: {manifest.get('format')!r}")
    return manifest


def unpack_heslnk(
    archive_path: str | Path,
    out_dir: str | Path,
    manifest_path: str | Path | None = None,
) -> HeslnkArchive:
    raw, archive = read_heslnk(archive_path)
    out_root = _as_path(out_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    for entry in archive.entries:
        data = decompress_entry(raw, entry)
        out_path = _safe_output_path(out_root, entry.name)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)

    manifest = archive_manifest(archive_path, raw, archive, include_hashes=True)
    write_manifest(manifest, manifest_path or out_root / MANIFEST_NAME)
    return archive


def _manifest_int(manifest: dict[str, Any], key: str, default: int) -> int:
    value = manifest.get(key, default)
    if not isinstance(value, int):
        raise HeslnkFormatError(f"manifest field {key!r} must be an integer")
    return value


def _manifest_bytes(manifest: dict[str, Any], key: str, size: int) -> bytes:
    value = manifest.get(key, "")
    if value == "":
        return b"\0" * size
    data = bytes.fromhex(value)
    if len(data) != size:
        raise HeslnkFormatError(f"manifest field {key!r} must be {size} bytes")
    return data


def _entry_padding(entry_manifest: dict[str, Any] | None) -> bytes:
    if not entry_manifest:
        return b"\0" * 4
    value = entry_manifest.get("entry_padding", "")
    if value == "":
        return b"\0" * 4
    data = bytes.fromhex(value)
    if len(data) != 4:
        raise HeslnkFormatError("manifest entry_padding must be 4 bytes")
    return data


def _load_names_from_dir(in_dir: Path) -> list[dict[str, Any]]:
    files = sorted(in_dir.glob("*.hese"))
    if not files:
        raise HeslnkFormatError(f"no .hese files found in {in_dir}")
    return [
        {
            "index": index,
            "name": path.stem,
            "hlzs_version": HLZS_VERSION,
            "entry_padding": "",
        }
        for index, path in enumerate(files)
    ]


def _source_block_if_unchanged(
    reuse_raw: bytes | None,
    reuse_archive: HeslnkArchive | None,
    entry_manifest: dict[str, Any],
    data: bytes,
) -> bytes | None:
    if reuse_raw is None or reuse_archive is None:
        return None
    original_sha = entry_manifest.get("sha256")
    if original_sha is None or sha256(data) != original_sha:
        return None

    index = entry_manifest["index"]
    if not isinstance(index, int) or index >= len(reuse_archive.entries):
        return None
    reuse_entry = reuse_archive.entries[index]
    if reuse_entry.name != entry_manifest.get("name"):
        return None
    if decompress_entry(reuse_raw, reuse_entry) != data:
        return None
    return read_hlzs_block(reuse_raw, reuse_entry)


def repack_heslnk(
    in_dir: str | Path,
    archive_path: str | Path,
    manifest_path: str | Path | None = None,
    reuse_from: str | Path | None = None,
    allow_no_manifest: bool = False,
) -> HeslnkArchive:
    in_root = _as_path(in_dir)
    manifest_file = _as_path(manifest_path) if manifest_path else in_root / MANIFEST_NAME
    reuse_raw: bytes | None = None
    reuse_archive: HeslnkArchive | None = None
    if reuse_from:
        reuse_raw, reuse_archive = read_heslnk(reuse_from)

    manifest: dict[str, Any] | None = None
    if manifest_file.exists():
        manifest = load_manifest(manifest_file)
        entry_manifests = list(manifest["entries"])
    elif reuse_raw is not None and reuse_archive is not None:
        manifest = archive_manifest(reuse_from, reuse_raw, reuse_archive, include_hashes=True)
        entry_manifests = list(manifest["entries"])
    elif allow_no_manifest:
        entry_manifests = _load_names_from_dir(in_root)
    else:
        raise HeslnkFormatError(
            f"manifest not found: {manifest_file}. "
            "Run unpack first, pass --manifest, use --reuse-from, or use --no-manifest."
        )

    version = _manifest_int(manifest or {}, "version", HESL_VERSION)
    unknown1 = _manifest_int(manifest or {}, "unknown1", 0)
    unknown2 = _manifest_int(manifest or {}, "unknown2", 0)
    header_padding1 = _manifest_bytes(manifest or {}, "header_padding1", 12)
    header_padding2 = _manifest_bytes(manifest or {}, "header_padding2", 8)

    count = len(entry_manifests)
    name_offset = HESL_HEADER_SIZE + count * HESL_ENTRY_SIZE
    names: list[str] = []
    blocks: list[bytes] = []
    unpacked_sizes: list[int] = []
    entry_paddings: list[bytes] = []

    for index, entry_manifest in enumerate(entry_manifests):
        name = entry_manifest.get("name")
        if not isinstance(name, str) or not name:
            raise HeslnkFormatError(f"manifest entry {index} has an invalid name")
        names.append(name)

        item_path = _safe_output_path(in_root, name)
        if not item_path.exists():
            raise HeslnkFormatError(f"missing unpacked file: {item_path}")
        data = item_path.read_bytes()

        reused_block = _source_block_if_unchanged(
            reuse_raw, reuse_archive, entry_manifest, data
        )
        if reused_block is not None:
            block = reused_block
        else:
            hlzs_version = entry_manifest.get("hlzs_version", HLZS_VERSION)
            if not isinstance(hlzs_version, int):
                raise HeslnkFormatError(f"entry {index} has invalid hlzs_version")
            block = make_hlzs_block(data, hlzs_version)

        blocks.append(block)
        unpacked_sizes.append(len(data))
        entry_paddings.append(_entry_padding(entry_manifest))

    name_blob = b"".join(name.encode("utf-8") + b"\0" for name in names)
    data_offset = align(name_offset + len(name_blob))

    offsets: list[int] = []
    cursor = data_offset
    for block in blocks:
        offsets.append(cursor)
        cursor = align(cursor + len(block))

    entries_raw = bytearray()
    for name, offset, unpacked_size, entry_padding in zip(
        names, offsets, unpacked_sizes, entry_paddings, strict=True
    ):
        entries_raw.extend(struct.pack("<III", name_hash(name), offset, unpacked_size))
        entries_raw.extend(entry_padding)

    header = (
        struct.pack("<4sIIII", HESL_MAGIC, version, count, unknown1, unknown2)
        + header_padding1
        + struct.pack("<II", name_offset, data_offset)
        + header_padding2
    )
    if len(header) != HESL_HEADER_SIZE:
        raise AssertionError("internal error: invalid HESL header size")

    out = bytearray(header)
    out.extend(entries_raw)
    out.extend(name_blob)
    out.extend(b"\0" * (data_offset - len(out)))
    if len(out) != data_offset:
        raise AssertionError("internal error: data offset miscalculated")

    for block in blocks:
        out.extend(block)
        out.extend(b"\0" * (align(len(out)) - len(out)))

    archive_out = _as_path(archive_path)
    archive_out.write_bytes(out)
    return parse_heslnk(bytes(out))


def verify_heslnk(source_dir: str | Path, archive_path: str | Path) -> list[str]:
    raw, archive = read_heslnk(archive_path)
    source_root = _as_path(source_dir)
    errors: list[str] = []
    for entry in archive.entries:
        expected_path = _safe_output_path(source_root, entry.name)
        if not expected_path.exists():
            errors.append(f"missing {expected_path}")
            continue
        expected = expected_path.read_bytes()
        actual = decompress_entry(raw, entry)
        if actual != expected:
            errors.append(
                f"{entry.name}.hese differs: archive {len(actual)} bytes, "
                f"source {len(expected)} bytes"
            )
    return errors
