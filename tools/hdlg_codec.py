#!/usr/bin/env python3
"""Codec helpers for HLZS-wrapped HDGL dialog tables."""

from __future__ import annotations

import struct
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path


HLZS_MAGIC = b"HLZS"
HDGL_MAGIC = b"HDGL"
HLZS_VERSION = 0x00001000
HDGL_VERSION = 0x00010000
LZSS_N = 4096
LZSS_F = 18
LZSS_THRESHOLD = 2
LZSS_INITIAL_R = LZSS_N - LZSS_F
HDGL_ALIGN = 32


class HdlgFormatError(ValueError):
    """Raised when an HLZS/HDGL file does not match the expected format."""


@dataclass(frozen=True)
class HdlgEntry:
    index: int
    offset: int
    text: str
    raw: bytes

    @property
    def byte_len(self) -> int:
        return len(self.raw)


@dataclass(frozen=True)
class HdglTable:
    version: int
    count: int
    data_offset: int
    entries: tuple[HdlgEntry, ...]
    raw_size: int
    trailing_size: int


def _as_path(path: str | Path) -> Path:
    return path if isinstance(path, Path) else Path(path)


def _align(value: int, alignment: int = HDGL_ALIGN) -> int:
    return (value + alignment - 1) // alignment * alignment


def lzss_decompress(payload: bytes, initial: int = 0) -> bytes:
    """Decompress the LZSS variant used by HLZS payloads."""

    if not 0 <= initial <= 0xFF:
        raise ValueError("initial must be a byte value")

    text = bytearray([initial]) * (LZSS_N + LZSS_F - 1)
    r = LZSS_INITIAL_R
    out = bytearray()
    pos = 0

    while pos < len(payload):
        flags = payload[pos]
        pos += 1

        for _ in range(8):
            if pos >= len(payload):
                break

            if flags & 1:
                c = payload[pos]
                pos += 1
                out.append(c)
                text[r] = c
                r = (r + 1) & (LZSS_N - 1)
            else:
                if pos + 1 >= len(payload):
                    raise HdlgFormatError("truncated LZSS back-reference")
                low = payload[pos]
                high_len = payload[pos + 1]
                pos += 2

                ref_pos = low | ((high_len & 0xF0) << 4)
                length = (high_len & 0x0F) + LZSS_THRESHOLD + 1
                for i in range(length):
                    c = text[(ref_pos + i) & (LZSS_N - 1)]
                    out.append(c)
                    text[r] = c
                    r = (r + 1) & (LZSS_N - 1)

            flags >>= 1

    return bytes(out)


def lzss_compress(data: bytes, max_candidates: int = 96) -> bytes:
    """Compress bytes into the LZSS stream accepted by HLZS.

    The compressor is intentionally conservative: it only references bytes that
    already exist in the sliding 4 KiB window. It does not need to reproduce the
    original compressed bytes, only a stream that decompresses to the same HDGL.
    """

    if max_candidates < 1:
        raise ValueError("max_candidates must be positive")

    positions: dict[int, deque[int]] = defaultdict(deque)
    out = bytearray()
    i = 0
    data_len = len(data)

    def key_at(offset: int) -> int:
        return (data[offset] << 16) | (data[offset + 1] << 8) | data[offset + 2]

    def add_position(offset: int) -> None:
        if offset + 2 < data_len:
            positions[key_at(offset)].append(offset)

    def find_match(offset: int) -> tuple[int, int]:
        if offset + LZSS_THRESHOLD >= data_len:
            return 0, 0

        candidates = positions.get(key_at(offset))
        if not candidates:
            return 0, 0

        min_pos = max(0, offset - LZSS_N)
        while candidates and candidates[0] < min_pos:
            candidates.popleft()

        best_pos = 0
        best_len = 0
        checked = 0
        max_len = min(LZSS_F, data_len - offset)

        for candidate in reversed(candidates):
            checked += 1
            if checked > max_candidates:
                break

            length = 0
            while (
                length < max_len
                and data[candidate + length] == data[offset + length]
            ):
                length += 1

            if length > best_len:
                best_pos = candidate
                best_len = length
                if best_len == max_len:
                    break

        if best_len <= LZSS_THRESHOLD:
            return 0, 0
        return best_pos, best_len

    while i < data_len:
        flags = 0
        chunk = bytearray()

        for bit in range(8):
            if i >= data_len:
                break

            match_pos, match_len = find_match(i)
            if match_len:
                ring_pos = (LZSS_INITIAL_R + match_pos) & (LZSS_N - 1)
                length_code = match_len - LZSS_THRESHOLD - 1
                chunk.append(ring_pos & 0xFF)
                chunk.append(((ring_pos >> 4) & 0xF0) | length_code)

                for add_i in range(i, i + match_len):
                    add_position(add_i)
                i += match_len
            else:
                flags |= 1 << bit
                chunk.append(data[i])
                add_position(i)
                i += 1

        out.append(flags)
        out.extend(chunk)

    return bytes(out)


def read_hlzs(path: str | Path) -> tuple[bytes, int]:
    """Read an HLZS file and return ``(decompressed_data, version)``."""

    raw = _as_path(path).read_bytes()
    if len(raw) < 32:
        raise HdlgFormatError("file is too small for an HLZS header")

    magic, version, compressed_size, uncompressed_size = struct.unpack(
        "<4sIII16x", raw[:32]
    )
    if magic != HLZS_MAGIC:
        raise HdlgFormatError(f"unexpected HLZS magic: {magic!r}")

    end = 32 + compressed_size
    if end > len(raw):
        raise HdlgFormatError("HLZS compressed payload is truncated")

    data = lzss_decompress(raw[32:end], 0)
    if len(data) != uncompressed_size:
        raise HdlgFormatError(
            "HLZS size mismatch: "
            f"expected {uncompressed_size}, decompressed {len(data)}"
        )

    return data, version


def write_hlzs(data: bytes, path: str | Path, version: int = HLZS_VERSION) -> None:
    """Write data as an HLZS-compressed file."""

    compressed = lzss_compress(data)
    header = struct.pack(
        "<4sIII16x",
        HLZS_MAGIC,
        version,
        len(compressed),
        len(data),
    )
    _as_path(path).write_bytes(header + compressed)


def read_hdlg(path: str | Path) -> tuple[bytes, int, HdglTable]:
    """Read an HLZS-wrapped HDGL file."""

    data, hlzs_version = read_hlzs(path)
    return data, hlzs_version, parse_hdgl(data)


def write_hdlg(
    texts: list[str] | tuple[str, ...],
    path: str | Path,
    hdgl_version: int = HDGL_VERSION,
    hlzs_version: int = HLZS_VERSION,
) -> bytes:
    """Build an HDGL table from text strings, wrap it in HLZS, and return HDGL bytes."""

    hdgl = build_hdgl(texts, hdgl_version)
    write_hlzs(hdgl, path, hlzs_version)
    return hdgl


def parse_hdgl(data: bytes) -> HdglTable:
    if len(data) < 32:
        raise HdlgFormatError("data is too small for an HDGL header")

    magic, version, count, data_offset = struct.unpack("<4sIII", data[:16])
    if magic != HDGL_MAGIC:
        raise HdlgFormatError(f"unexpected HDGL magic: {magic!r}")

    table_end = 32 + count * 4
    if data_offset < table_end:
        raise HdlgFormatError(
            f"HDGL data offset {data_offset:#x} overlaps offset table ending at {table_end:#x}"
        )
    if data_offset > len(data):
        raise HdlgFormatError("HDGL data offset points past end of file")

    offset_bytes = data[32:table_end]
    offsets = struct.unpack(f"<{count}I", offset_bytes) if count else ()
    entries: list[HdlgEntry] = []
    last_string_end = data_offset

    for index, relative_offset in enumerate(offsets):
        start = data_offset + relative_offset
        if start >= len(data):
            raise HdlgFormatError(
                f"entry {index} starts past end of HDGL data: {start:#x}"
            )

        end = data.find(b"\0", start)
        if end == -1:
            raise HdlgFormatError(f"entry {index} is missing a NUL terminator")

        raw = data[start:end]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HdlgFormatError(f"entry {index} is not valid UTF-8: {exc}") from exc

        entries.append(HdlgEntry(index, relative_offset, text, raw))
        last_string_end = max(last_string_end, end + 1)

    return HdglTable(
        version=version,
        count=count,
        data_offset=data_offset,
        entries=tuple(entries),
        raw_size=len(data),
        trailing_size=len(data) - last_string_end,
    )


def build_hdgl(
    texts: list[str] | tuple[str, ...],
    version: int = HDGL_VERSION,
    alignment: int = HDGL_ALIGN,
) -> bytes:
    """Build an HDGL string table from a sequence of text strings."""

    count = len(texts)
    data_offset = 32 + count * 4
    offsets: list[int] = []
    blob = bytearray()

    for index, text in enumerate(texts):
        if text is None:
            raise HdlgFormatError(f"entry {index} text is None")
        if not isinstance(text, str):
            text = str(text)
        raw = text.encode("utf-8")
        if b"\0" in raw:
            raise HdlgFormatError(f"entry {index} contains a NUL byte")

        offsets.append(len(blob))
        blob.extend(raw)
        blob.append(0)

    header = struct.pack("<4sIII16x", HDGL_MAGIC, version, count, data_offset)
    table = struct.pack(f"<{count}I", *offsets) if count else b""
    raw_hdgl = bytearray(header + table + blob)
    padded_len = _align(len(raw_hdgl), alignment)
    raw_hdgl.extend(b"\0" * (padded_len - len(raw_hdgl)))
    return bytes(raw_hdgl)


def table_texts(table: HdglTable) -> list[str]:
    return [entry.text for entry in table.entries]


def compare_tables(left: HdglTable, right: HdglTable) -> list[str]:
    """Return human-readable differences between two parsed HDGL tables."""

    diffs: list[str] = []
    if left.count != right.count:
        diffs.append(f"count differs: {left.count} != {right.count}")
        return diffs

    for left_entry, right_entry in zip(left.entries, right.entries, strict=True):
        if left_entry.text != right_entry.text:
            diffs.append(
                f"id {left_entry.index}: {left_entry.text!r} != {right_entry.text!r}"
            )
            if len(diffs) >= 20:
                diffs.append("more differences omitted")
                break
    return diffs
