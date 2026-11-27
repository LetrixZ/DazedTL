"""Unpack and repack the images of SAS5 (solfasys) games' .iar archives.

  sas5_iar.py list   <archive.iar> [--sec5 game.sec5]
  sas5_iar.py unpack <archive.iar> <out dir> [--sec5 game.sec5] [--only TEXT]
  sas5_iar.py pack   <original.iar> <png dir> <out.iar>

Needs Pillow. Entries are compressed bitmaps, never JPEG or PNG files: what a
search for their signatures finds in an archive is compressed data.

`unpack` writes `<index>[_<name>].png`; names come from the .sec5's RES2
or older RESR section, and `--only` keeps the entries whose file name holds TEXT. A large
archive takes minutes. `pack` replaces the entries of the PNGs in a folder,
found by the index their file names start with, so keep only changed ones
there. It builds from the original, keeps every other entry's bytes and
requires each picture's size and color mode to stay. Layered and delta
entries, and 8-bit ones with a palette, are listed but not packed; the first
two aren't unpacked either.
"""

import argparse
import re
import struct
import sys
from pathlib import Path

from PIL import Image

HEADERS = {1: 0x30, 2: 0x40, 3: 0x40, 4: 0x48}
LAYERED, DELTA = 0x1000, 0x800
DEPTHS = {0x02: 1, 0x1C: 3, 0x3C: 4}
MODES = {1: "L", 3: "RGB", 4: "RGBA"}
RAW = {1: "L", 3: "BGR", 4: "BGRA"}
WINDOW, LONGEST = 0x2000, 17 + 255


class IarError(ValueError):
    pass


def decompress(data, size):
    """The *size* bytes an entry's LZ stream holds."""
    out, at, bits = bytearray(), 0, 1

    def bit():
        nonlocal at, bits
        if bits == 1:
            bits = data[at] | data[at + 1] << 8 | 0x10000
            at += 2
        value = bits & 1
        bits >>= 1
        return value

    def byte():
        nonlocal at
        at += 1
        return data[at - 1]

    try:
        while len(out) < size:
            if bit():
                out.append(byte())
                continue
            if bit():
                high = bit()
                if bit():
                    base = 1
                elif bit():
                    base = 0x201
                else:
                    high = high << 1 | bit()
                    if bit():
                        base = 0x401
                    else:
                        high = high << 1 | bit()
                        if bit():
                            base = 0x801
                        else:
                            high = high << 1 | bit()
                            base = 0x1001
                offset = base + (high << 8 | byte())
                for count in (3, 4, 5, 6):
                    if bit():
                        break
                else:
                    if bit():
                        count = 7 + bit()
                    elif bit():
                        count = 17 + byte()
                    else:
                        count = 9 + (bit() << 2 | bit() << 1 | bit())
            else:
                count = 2
                if bit():
                    high = bit() << 2 | bit() << 1 | bit()
                    offset = 0x100 + (high << 8 | byte())
                else:
                    offset = 1 + byte()
                    if offset == 0x100:
                        break
            start = len(out) - offset
            if start < 0:
                raise IarError("The data reaches before its start.")
            if offset >= count:
                out += out[start : start + count]
            else:
                piece = out[start:]
                out += (piece * (count // offset + 1))[:count]
    except IndexError:
        raise IarError("The compressed data ends early.") from None
    if len(out) != size:
        raise IarError(f"An entry holds {len(out)} bytes, not {size}.")
    return bytes(out)


def compress(data):
    """An LZ stream that decompress (and the engine) reads as *data*."""
    out, word, used = bytearray(), 0, 16

    def put(*values):
        nonlocal word, used
        for value in values:
            if used == 16:
                word, used = len(out), 0
                out.extend(b"\0\0")
            if value:
                out[word + (used >> 3)] |= 1 << (used & 7)
            used += 1

    def put_long(offset, count):
        put(0, 1)
        for base, extra, prefix in (
            (0x1, 1, (1,)),
            (0x201, 1, (0, 1)),
            (0x401, 2, (0, 0)),
            (0x801, 3, (0, 0)),
            (0x1001, 4, (0, 0)),
        ):
            if offset - base < extra_range[extra]:
                break
        value = offset - base
        high = value >> 8
        top = [high >> n & 1 for n in range(extra - 1, -1, -1)]
        put(top[0], *prefix)
        if extra == 2:
            put(top[1], 1)
        elif extra == 3:
            put(top[1], 0, top[2], 1)
        elif extra == 4:
            put(top[1], 0, top[2], 0, top[3])
        out.append(value & 0xFF)
        if count < 7:
            put(*[0] * (count - 3), 1)
        elif count < 9:
            put(0, 0, 0, 0, 1, count - 7)
        elif count < 17:
            rest = count - 9
            put(0, 0, 0, 0, 0, 0, rest >> 2, rest >> 1 & 1, rest & 1)
        else:
            put(0, 0, 0, 0, 0, 1)
            out.append(count - 17)

    extra_range = {1: 0x200, 2: 0x400, 3: 0x800, 4: 0x1000}
    size, at, seen = len(data), 0, {}
    while at < size:
        best, offset = 1, 0
        key = data[at : at + 3]
        for start in reversed(seen.get(key, ())):
            if at - start > WINDOW:
                break
            limit = min(LONGEST, size - at)
            if best >= limit:
                break
            if data[start + best] != data[at + best]:
                continue
            count = 3
            while count < limit and data[start + count] == data[at + count]:
                count += 1
            if count > best:
                best, offset = count, at - start
        if best >= 3:
            put_long(offset, best)
        else:
            put(1)
            out.append(data[at])
            best = 1
        # Long runs are indexed sparsely; their every position matches alike.
        for spot in range(at, at + best, 1 if best < 32 else 8):
            places = seen.setdefault(data[spot : spot + 3], [])
            places.append(spot)
            if len(places) > 32:
                del places[:16]
        at += best
    put(0, 0, 0)
    out.append(0xFF)
    return bytes(out)


class Entry:
    def __init__(self, archive, index, start, end):
        raw = archive.raw
        if end - start < archive.header:
            raise IarError(f"Entry {index} is cut short.")
        self.index, self.data = index, raw[start:end]
        self.flags, _, self.packed = struct.unpack_from("<HBB", self.data)
        self.size, self.palette, self.stored = struct.unpack_from("<III", self.data, 8)
        self.x, self.y, self.width, self.height, self.stride = struct.unpack_from(
            "<iiIII", self.data, 0x18
        )
        self.body = archive.header + self.palette

    @property
    def kind(self):
        if self.flags & LAYERED:
            return "layered"
        if self.flags & DELTA:
            return "delta"
        depth = DEPTHS.get(self.flags & 0x3E)
        if depth is None:
            return f"unknown flags {self.flags:#x}"
        return f"{depth * 8}-bit" + (" palette" if self.palette else "")

    @property
    def depth(self):
        kind = self.kind
        if not kind.endswith(("-bit", "palette")):
            raise IarError(f"Entry {self.index} is {kind}.")
        return DEPTHS[self.flags & 0x3E]

    def image(self):
        depth = self.depth
        pixels = self.data[self.body : self.body + self.stored]
        if self.packed:
            pixels = decompress(pixels, self.size)
        need = self.stride * self.height
        if self.stride < self.width * depth or len(pixels) < need:
            raise IarError(f"Entry {self.index} holds too few pixels.")
        size = (self.width, self.height)
        picture = Image.frombytes(
            MODES[depth], size, pixels[:need], "raw", RAW[depth], self.stride
        )
        if self.palette:
            colors = self.data[self.body - self.palette : self.body]
            picture = picture.convert("L")
            picture.putpalette(colors, "BGRX")
        return picture

    def replaced(self, picture):
        """The entry's bytes showing *picture*."""
        depth = self.depth
        if self.palette:
            raise IarError(f"Entry {self.index} uses a palette.")
        if picture.size != (self.width, self.height):
            raise IarError(
                f"Entry {self.index} is {self.width}x{self.height}; its picture is "
                f"{picture.width}x{picture.height}."
            )
        if picture.mode != MODES[depth]:
            raise IarError(
                f"Entry {self.index} is {MODES[depth]}; its picture is {picture.mode}."
            )
        row = self.width * depth
        pixels = picture.tobytes("raw", RAW[depth])
        padding = bytes(self.stride - row)
        pixels = b"".join(
            pixels[at : at + row] + padding for at in range(0, len(pixels), row)
        )
        body = compress(pixels)
        head = bytearray(self.data[: self.body])
        head[3] = 1
        struct.pack_into("<I", head, 8, len(pixels))
        struct.pack_into("<I", head, 0x10, len(body))
        return bytes(head) + body


class Archive:
    def __init__(self, raw):
        if raw[:4] != b"iar " or len(raw) < 0x20:
            raise IarError("This is not an IAR archive.")
        self.raw, self.version = raw, struct.unpack_from("<H", raw, 4)[0]
        if self.version not in HEADERS:
            raise IarError(f"IAR version {self.version} is not supported.")
        self.header = HEADERS[self.version]
        count, slots = struct.unpack_from("<II", raw, 0x18)
        self.code = "I" if self.version < 3 else "Q"
        self.first = 0x20 + slots * struct.calcsize(self.code)
        if count > slots or self.first > len(raw):
            raise IarError("The archive's index is damaged.")
        offsets = struct.unpack_from(f"<{count}{self.code}", raw, 0x20)
        ends = [*offsets[1:], len(raw)]
        if any(
            not self.first <= start <= end
            for start, end in zip(offsets, ends, strict=True)
        ):
            raise IarError("The archive's entries are out of order.")
        self.offsets = offsets
        self.entries = [
            Entry(self, index, start, end)
            for index, (start, end) in enumerate(zip(offsets, ends, strict=True))
        ]

    def packed(self, pictures):
        """The archive's bytes with {index: picture} in place."""
        unknown = set(pictures) - set(range(len(self.entries)))
        if unknown:
            raise IarError(f"No entry {min(unknown)}.")
        out = bytearray(self.raw[: self.offsets[0] if self.offsets else len(self.raw)])
        offsets = []
        for entry in self.entries:
            offsets.append(len(out))
            picture = pictures.get(entry.index)
            out += entry.data if picture is None else entry.replaced(picture)
        struct.pack_into(f"<{len(offsets)}{self.code}", out, 0x20, *offsets)
        return bytes(out)


def older_names(section, archive):
    """names() for a RESR section."""
    at, found = 4, {}
    for _ in range(struct.unpack_from("<I", section)[0]):
        name, _, _, rest = section[at:].split(b"\0", 3)
        at = len(section) - len(rest)
        size = struct.unpack_from("<I", section, at)[0]
        path, _, tail = section[at + 4 : at + 4 + size].partition(b"\0")
        at += 4 + size
        if len(tail) >= 4 and path.decode("cp932").casefold() == archive.casefold():
            index = struct.unpack_from("<I", tail)[0]
            found.setdefault(index, name.decode("cp932", "replace"))
    return found


def names(sec5, archive):
    """{entry index: resource name} of *archive* in a sec5's RES2 section."""
    at, section, older = 8, None, None
    while at + 8 <= len(sec5):
        size = struct.unpack_from("<I", sec5, at + 4)[0]
        if sec5[at : at + 4] == b"RES2":
            section = sec5[at + 8 : at + 8 + size]
        elif sec5[at : at + 4] == b"RESR":
            older = sec5[at + 8 : at + 8 + size]
        at += 8 + size
    if section is None:
        return older_names(older, archive) if older else {}
    size = struct.unpack_from("<I", section)[0]
    pool, at = section[4 : 4 + size], 4 + size

    def value():
        nonlocal at
        kind = section[at]
        at += 1
        if not kind & 0xE0:
            return (kind & 0xF) - (kind & 0x10)
        if kind & 0xF8 not in (0x80, 0x90):
            raise IarError(f"An unknown RES2 value {kind:#x}.")
        width = (kind & 7) + 1
        number = int.from_bytes(section[at : at + width], "little", signed=True)
        at += width
        if kind & 0xF8 == 0x80:
            return number
        length = struct.unpack_from("<I", pool, number)[0]
        return pool[number + 4 : number + 4 + length].decode("cp932", "replace")

    count = struct.unpack_from("<I", section, at)[0]
    at += 4
    found = {}
    for _ in range(count):
        name, _, _ = value(), value(), value()
        fields = {}
        for _ in range(value()):
            key = value()
            fields[key] = value()
        if str(fields.get("path", "")).casefold() == archive.casefold():
            found.setdefault(fields.get("arc-index"), name)
    return found


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, *extra in (("list",), ("unpack", "out"), ("pack", "pngs", "out")):
        command = commands.add_parser(name)
        for argument in ("archive", *extra):
            command.add_argument(argument, type=Path)
        if name != "pack":
            command.add_argument("--sec5", type=Path)
        if name == "unpack":
            command.add_argument("--only", default="")
    args = parser.parse_args()
    archive = Archive(args.archive.read_bytes())
    if args.command == "pack":
        pictures = {}
        for path in sorted(args.pngs.glob("*.png")):
            index = re.match(r"\d+", path.name)
            if not index or int(index[0]) in pictures:
                raise IarError(f"{path.name} must start with its own entry's index.")
            with Image.open(path) as picture:
                pictures[int(index[0])] = picture.copy()
        if not pictures:
            raise IarError("The folder holds no PNG files.")
        args.out.write_bytes(archive.packed(pictures))
        print(f"{len(pictures)} of {len(archive.entries)} entries replaced")
        return 0
    labels = names(args.sec5.read_bytes(), args.archive.name) if args.sec5 else {}
    skipped = 0
    for entry in archive.entries:
        label = re.sub(r'[\\/:*?"<>|]', "_", labels.get(entry.index, ""))
        name = f"{entry.index:04d}" + (f"_{label}" if label else "")
        if args.command == "list":
            print(
                f"{name} {entry.width}x{entry.height} at {entry.x},{entry.y} {entry.kind}"
            )
        elif args.only in name:
            args.out.mkdir(parents=True, exist_ok=True)
            try:
                entry.image().save(args.out / f"{name}.png")
            except IarError as exc:
                print(f"SKIPPED {name}: {exc}")
                skipped += 1
    return 1 if skipped else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except IarError as exc:
        sys.exit(str(exc))
