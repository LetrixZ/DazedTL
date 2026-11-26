"""Extract and rebuild the text of SAS5 (solfasys) games' .sec5 scenario files.

Reads sec5 versions 107000 to 110000; `references/engine-sas5.md` names the
tools for older ones. Needs only Python's standard library.

  sas5_text.py info   <game.sec5>
  sas5_text.py check  <game.sec5>
  sas5_text.py decode <game.sec5> <out dir>
  sas5_text.py units  <game.sec5> <units.json>
  sas5_text.py patch  <original.sec5> <translations.json> <out.sec5> [--codepage N]

`check` reads every byte of CODE, reports its addresses and proves that a
rebuild without changes gives the same file; run it before anything else.
`decode` writes the text as UTF-8 for DazedTL's census.

Unit IDs are offsets in the original's decoded CODE, or a path in OPTN:
`CODE/<hex>` for a message (its lines joined by "\\n") or a string in an
expression, and `OPTN/<path>` for an option. A string's "type" is the DTDE
record it fills, which tells nameplates and choices from identifiers, and a
message's "context" holds the strings set since the message before it.
Not every string is player text: pass only reviewed ones to `patch`, which
takes {unit ID: translation} and always builds from the original.

A changed length moves everything after it, so `patch` rewrites every address
of CODE, the script offsets (CZIT) and the event handlers (OPTN EXPORT_FUNS),
and refuses the build when an address is not the start of an instruction.
`--codepage` sets the code pages in CHAR and writes the text in that one.
"""

import argparse
import json
import struct
import sys
from bisect import bisect_left
from itertools import accumulate
from pathlib import Path

# Older files' lengths take four bytes, and their 19 operands are addresses.
LEGACY = 109000
VERSIONS = (107000, 108000, 109000, 109002, 110000)
DIRECT = {0x08, 0x0B, 0x0E, 0x70}
RELATIVE = {0x09, 0x0C, 0x0F, 0x12}
SIZES = {
    **dict.fromkeys(DIRECT | RELATIVE, 4),
    **dict.fromkeys((0x13, 0x17, 0x29, 0x2A, 0x53, 0x55, 0x58, 0x59), 1),
    **dict.fromkeys((0x60, 0x61, 0x79, 0x7B), 1),
    **dict.fromkeys((0x14, 0x18), 2),
    **dict.fromkeys((0x15, 0x19, 0x1B, 0x20, 0x5A, 0x78), 4),
    **dict.fromkeys((0x16, 0x1A, 0x1C), 8),
    0xFE: 5,
}
PLAIN = {
    0x0A, 0x0D, 0x10, 0x11, 0x21, 0x23, 0x24, 0x25, 0x26, 0x28, 0x50, 0x51,
    0x52, 0x54, 0x56, 0x57, 0x5B, 0x5C, 0x5D, 0x5E, 0x62, 0x63, 0x64, 0x65,
    *range(0x30, 0x45), *range(0x71, 0x76), *range(0x80, 0xC6),
    *range(0xC8, 0xE6), 0xFF,
}  # fmt: skip
RETURN = 0x11
BREAK = b"\x1b\xf8\x01\xff"


class SAS5Error(ValueError):
    pass


def u32(data, at):
    if at < 0 or at + 4 > len(data):
        raise SAS5Error(f"Unexpected end of data at {at:#x}.")
    return struct.unpack_from("<I", data, at)[0]


def cipher(data, encode=False):
    """The CODE section's rolling XOR, keyed on the bytes as stored."""
    key, out = 0, bytearray(len(data))
    for at, byte in enumerate(data):
        out[at] = byte ^ key
        key = (key + (out[at] if encode else byte) + 0x12) & 0xFF
    return bytes(out)


def codec(codepage):
    return {932: "cp932", 65001: "utf-8"}.get(codepage, f"cp{codepage}")


class Reader:
    def __init__(self, data):
        self.data, self.at = data, 0

    def take(self, size):
        if size < 0 or self.at + size > len(self.data):
            raise SAS5Error(f"Unexpected end of data at {self.at:#x}.")
        self.at += size
        return self.data[self.at - size : self.at]

    def byte(self):
        return self.take(1)[0]

    def int(self):
        return u32(self.take(4), 0)

    def cstring(self):
        end = self.data.find(b"\0", self.at)
        if end < 0:
            raise SAS5Error(f"Unterminated name at {self.at:#x}.")
        return self.take(end + 1 - self.at)[:-1]


# OPTN: a map (0), tuple (1), integer (2), string (3), resource (4), label (5)
# or binary (6), kept as (type, value) so that writing gives the same bytes.
def read_option(reader):
    kind = reader.byte()
    if kind == 0:
        value = []
        for _ in range(reader.int()):
            key = reader.take(reader.int())
            value.append((key, read_option(reader)))
    elif kind == 1:
        value = [read_option(reader) for _ in range(reader.int())]
    elif kind in (2, 4, 5):
        value = reader.int()
    elif kind in (3, 6):
        value = reader.take(reader.int())
    else:
        raise SAS5Error(f"Unknown OPTN type {kind} at {reader.at - 1:#x}.")
    return kind, value


def write_option(option):
    kind, value = option
    out = bytes([kind])
    if kind == 0:
        out += struct.pack("<I", len(value))
        for key, child in value:
            out += struct.pack("<I", len(key)) + key + write_option(child)
    elif kind == 1:
        out += struct.pack("<I", len(value)) + b"".join(map(write_option, value))
    elif kind in (2, 4, 5):
        out += struct.pack("<I", value)
    else:
        out += struct.pack("<I", len(value)) + value
    return out


def map_option(option, change, path=""):
    """*option* with change(path, kind, value) applied to every leaf."""
    kind, value = option
    if kind == 0:
        return kind, [
            (key, map_option(child, change, f"{path}/{key.decode('cp932')}"))
            for key, child in value
        ]
    if kind == 1:
        return kind, [
            map_option(child, change, f"{path}/{n}") for n, child in enumerate(value)
        ]
    return kind, change(path, kind, value)


def read_types(data):
    """DTDE's (name, bytes one value takes in CODE) by index."""
    reader, types = Reader(data), []

    def size():
        kind = reader.byte()
        if kind == 0xFF:
            return types[reader.int()][1]
        if kind == 0:
            primitive = reader.byte()
            if primitive > 8:
                raise SAS5Error(f"Unknown primitive type {primitive}.")
            return (1, 2, 4, 8, 4, 8, 0, 0, 4)[primitive]
        if kind == 1:
            count = reader.int()
            return count * size()
        if kind == 3:
            total = 0
            for _ in range(reader.int()):
                reader.cstring()
                total += size()
            return total
        raise SAS5Error(f"Unknown DTDE type {kind} at {reader.at - 1:#x}.")

    for _ in range(reader.int()):
        name = reader.cstring().decode("cp932", "replace")
        types.append((name, size()))
    if reader.at != len(data):
        raise SAS5Error("DTDE holds more than its types.")
    return types


class Code:
    """Every instruction of a decoded CODE section.

    starts: offsets an address may name. addresses: (operand offset,
    relative). strings: (length field offset, its size, text offset, text
    end, type name). expressions: (length field offset, data start, data
    end). texts: (start, end) of raw message text. order: ("text" or
    "string", index) as they come.
    """

    def __init__(self, data, types, scripts, legacy=False):
        self.data, self.types = data, types
        self.width = 4 if legacy else 2
        self.direct = DIRECT | {0x19} if legacy else DIRECT
        self.starts, self.addresses, self.strings = set(), [], []
        self.expressions, self.texts, self.order = [], [], []
        at = 0
        # An instruction never crosses into the next script.
        for limit in sorted({at for _, at in scripts} | {len(data)}):
            while at < limit:
                at = self.item(at, limit)
        self.starts.add(len(data))

    def item(self, at, limit):
        lead = self.data[at]
        self.starts.add(at)
        if lead in (0x1A, 0xFF):
            return at + 1
        if lead == 0x1B:
            return self.command(at, limit)
        if lead == 0x20 or 0x81 <= lead <= 0xFD:
            return self.text(at, limit)
        # Library code is bare operations up to a return; what does not read
        # as one is text that starts with a half-width character.
        lists = (self.addresses, self.strings, self.order)
        counts, starts = [len(rows) for rows in lists], set()
        try:
            return self.library(at, limit, starts)
        except SAS5Error:
            for rows, count in zip(lists, counts, strict=True):
                del rows[count:]
            if lead < 0x20:
                raise
            starts.clear()
            return self.text(at, limit)
        finally:
            self.starts |= starts

    def text(self, at, limit):
        end = at
        while end < limit and self.data[end] not in (0x00, 0x1A, 0x1B):
            end += 1
        if end == limit or self.data[end] == 0:
            raise SAS5Error(f"Unreadable CODE at {at:#x}.")
        self.order.append(("text", len(self.texts)))
        self.texts.append((at, end))
        return end

    def command(self, at, limit):
        data, at = self.data, at + 3
        while at < limit and data[at] != 0xFF:
            start = at + 1 + self.width
            end = start + int.from_bytes(data[at + 1 : start], "little")
            if end > limit:
                raise SAS5Error(f"The expression at {at:#x} crosses its script's end.")
            self.expressions.append((at + 1, start, end))
            name, at = "", start
            while at < end:
                at, name = self.operation(at, end, name, self.starts)
        if at >= limit:
            raise SAS5Error(f"An unfinished command before {limit:#x}.")
        return at + 1

    def library(self, at, limit, starts):
        name, lead = "", None
        while lead != RETURN:
            lead = self.data[at] if at < limit else None
            at, name = self.operation(at, limit, name, starts)
        return at

    def operation(self, at, limit, name, starts):
        if at >= limit:
            raise SAS5Error(f"Unexpected end of code at {at:#x}.")
        data, op, here = self.data, self.data[at], at
        starts.add(here)
        at += 1
        if op in SIZES:
            size = SIZES[op]
            if op in self.direct or op in RELATIVE:
                self.addresses.append((at, op in RELATIVE))
            elif op in (0x78, 0x20) and at + 4 <= limit:
                index = u32(data, at)
                name = self.types[index][0] if index < len(self.types) else ""
        elif op == 0x7A:
            if at + 2 > limit or data[at]:
                raise SAS5Error(f"An unknown string kind at {at:#x}.")
            size = 2 + data[at + 1]
            self.string(at + 1, 1, at + 2, at + size, name)
        elif op in (0x1D, 0x1E):
            index = u32(data, at)
            if index >= len(self.types):
                raise SAS5Error(f"An unknown type at {at:#x}.")
            each = self.types[index][1]
            size = 4 + each if op == 0x1D else 8 + each * u32(data, at + 4)
            if op == 0x1E and each == 1:
                self.string(at + 4, 4, at + 8, at + size, name)
        elif op in PLAIN:
            size = 0
        else:
            raise SAS5Error(f"An unknown operation {op:#04x} at {here:#x}.")
        if at + size > limit:
            raise SAS5Error(f"The operand at {at:#x} crosses its end.")
        return at + size, name

    def string(self, field, size, start, end, name):
        self.order.append(("string", len(self.strings)))
        self.strings.append((field, size, start, end, name))

    def target(self, operand, relative):
        value = u32(self.data, operand)
        return (operand + 4 + value) & 0xFFFFFFFF if relative else value


class Sec5:
    def __init__(self, raw):
        if raw[:4] != b"SEC5":
            raise SAS5Error("This is not a SEC5 file.")
        self.raw, self.version = raw, u32(raw, 4)
        if self.version not in VERSIONS:
            raise SAS5Error(
                f"sec5 version {self.version} is not supported; "
                "references/engine-sas5.md names the tools for others."
            )
        reader, self.sections = Reader(raw), []
        reader.at = 8
        while reader.at < len(raw):
            tag = reader.take(4)
            self.sections.append((tag, reader.take(reader.int())))
        self.section = dict(self.sections)
        for tag in (b"CODE", b"CZIT", b"OPTN", b"DTDE", b"CHAR"):
            if tag not in self.section:
                raise SAS5Error(f"The file has no {tag.decode()} section.")
        reader = Reader(self.section[b"CZIT"])
        self.scripts = []
        for _ in range(reader.int()):
            self.scripts.append((reader.cstring(), reader.int()))
        self.czit_tail = reader.data[reader.at :]
        reader = Reader(self.section[b"OPTN"])
        self.options = read_option(reader)
        if reader.at != len(reader.data):
            raise SAS5Error("OPTN holds more than its tree.")
        self.codepage = u32(self.section[b"CHAR"], 0) or 932
        self.code = Code(
            cipher(self.section[b"CODE"]),
            read_types(self.section[b"DTDE"]),
            self.scripts,
            self.version < LEGACY,
        )

    def exports(self):
        """(path, address) of OPTN's event handlers and labels."""
        found = []

        def note(path, kind, value):
            if kind == 5 or (kind == 2 and path.startswith("/EXPORT_FUNS/")):
                found.append((path, value))
            return value

        map_option(self.options, note)
        return found

    def problems(self):
        """Addresses that do not name an instruction's start."""
        code = self.code
        rows = [
            (f"CODE {operand:#x}", code.target(operand, relative))
            for operand, relative in code.addresses
        ]
        rows += [(f"OPTN{path}", value) for path, value in self.exports()]
        return [
            f"{name} -> {value:#x}" for name, value in rows if value not in code.starts
        ]

    def script(self, at):
        name = b""
        for script, start in self.scripts:
            if start <= at:
                name = script
        return name.decode("cp932", "replace")

    def messages(self):
        """{start: (end, its lines' (start, end))} of raw text, by message."""
        found, last = {}, None
        for start, end in self.code.texts:
            if last is not None and self.code.data[found[last][0] : start] == BREAK:
                found[last] = end, [*found[last][1], (start, end)]
            else:
                last = start
                found[start] = end, [(start, end)]
        return found

    def units(self):
        code, encoding = self.code, codec(self.codepage)

        def read(start, end):
            return code.data[start:end].decode(encoding, "replace")

        messages, found, context = self.messages(), [], []
        for kind, index in code.order:
            if kind == "text":
                start = code.texts[index][0]
                if start in messages:
                    found.append(
                        {
                            "id": f"CODE/{start:06x}",
                            "kind": "message",
                            "script": self.script(start),
                            "text": "\n".join(read(*line) for line in messages[start][1]),
                            "context": context,
                        }
                    )
                    context = []
                continue
            _, _, start, end, name = code.strings[index]
            text = read(start, end)
            if text:
                context.append({"type": name, "text": text})
            if not text.isascii():
                found.append(
                    {
                        "id": f"CODE/{start:06x}",
                        "kind": "string",
                        "script": self.script(start),
                        "type": name,
                        "text": text,
                    }
                )

        def note(path, kind, value):
            if kind == 3 and not value.isascii():
                text = value.decode(encoding, "replace")
                found.append({"id": f"OPTN{path}", "kind": "option", "text": text})
            return value

        map_option(self.options, note)
        return found

    def rebuild(self, translations, codepage=None):
        """The file's bytes with *translations* and, if given, *codepage*."""
        problems = self.problems()
        if problems:
            raise SAS5Error("Unmapped addresses: " + "; ".join(problems[:5]))
        encoding, code = codec(codepage or self.codepage), self.code

        def encode(unit, text):
            try:
                data = text.encode(encoding)
            except UnicodeEncodeError as exc:
                raise SAS5Error(f"{unit}: {exc}") from exc
            # The engine reads a control byte in text as a command.
            if any(byte < 0x20 for byte in data):
                raise SAS5Error(f"{unit} holds a control character.")
            return data

        messages = self.messages()
        strings = {row[2]: row for row in code.strings}
        paths = set()
        map_option(self.options, lambda path, kind, _: paths.add((path, kind)))
        options, edits = {}, []
        for unit, text in translations.items():
            place, _, rest = unit.partition("/")
            if place == "OPTN" and ("/" + rest, 3) in paths:
                options["/" + rest] = encode(unit, text)
                continue
            try:
                start = int(rest, 16) if place == "CODE" else None
            except ValueError:
                start = None
            if start in messages:
                lines = text.replace("\r\n", "\n").split("\n")
                if not all(lines):
                    raise SAS5Error(f"{unit} has an empty line.")
                data = BREAK.join(encode(unit, line) for line in lines)
                edits.append((start, messages[start][0], data))
            elif start in strings:
                field, size, _, end, _ = strings[start]
                data = encode(unit, text)
                if size == 1 and len(data) > 255:
                    raise SAS5Error(f"{unit} takes {len(data)} bytes; 255 fit.")
                count = len(data).to_bytes(size, "little")
                edits += [(field, field + size, count), (start, end, data)]
            else:
                # A file already patched has its addresses moved.
                raise SAS5Error(
                    f"No unit {unit} in this file. Build from the original .sec5 "
                    "your units came from, which the game's Git keeps under "
                    "refs/dazedtl/originals, never from a patched one."
                )
        edits.sort()
        starts = [start for start, _, _ in edits]
        shifts = [0, *accumulate(len(data) - (end - start) for start, end, data in edits)]
        out, at = bytearray(), 0
        for start, end, data in edits:
            out += code.data[at:start] + data
            at = end
        out += code.data[at:]

        def new(old):
            """Where the byte at *old* of the original now is."""
            count = bisect_left(starts, old)
            if count and old < edits[count - 1][1]:
                raise SAS5Error(f"An address names the inside of edited text: {old:#x}.")
            return old + shifts[count]

        for operand, relative in code.addresses:
            value, spot = new(code.target(operand, relative)), new(operand)
            if relative:
                value = (value - spot - 4) & 0xFFFFFFFF
            struct.pack_into("<I", out, spot, value)
        for field, start, end in code.expressions:
            length = new(end) - new(start)
            if length > 0x7FFF:
                raise SAS5Error(f"The expression at {start:#x} is too long.")
            spot = new(field)
            out[spot : spot + code.width] = length.to_bytes(code.width, "little")
        czit = struct.pack("<I", len(self.scripts))
        for name, start in self.scripts:
            czit += name + b"\0" + struct.pack("<I", new(start))
        exports = {path for path, _ in self.exports()}

        def change(path, kind, value):
            if path in exports:
                return new(value)
            return options.get(path, value)

        changed = {
            b"CODE": cipher(bytes(out), encode=True),
            b"CZIT": czit + self.czit_tail,
            b"OPTN": write_option(map_option(self.options, change)),
        }
        if codepage:
            pages = struct.pack("<3I", codepage, codepage, codepage)
            changed[b"CHAR"] = pages + self.section[b"CHAR"][12:]
        result = self.raw[:8]
        for tag, data in self.sections:
            data = changed.get(tag, data)
            result += tag + struct.pack("<I", len(data)) + data
        return result


def check(sec5):
    code, problems = sec5.code, sec5.problems()
    print(
        f"{len(code.texts)} texts, {len(code.strings)} strings, "
        f"{len(code.addresses)} addresses, {len(sec5.exports())} exports"
    )
    for problem in problems:
        print("UNMAPPED", problem)
    if problems:
        return 1
    same = sec5.rebuild({}) == sec5.raw
    print("identity rebuild:", "same" if same else "DIFFERENT")
    return 0 if same else 1


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, *extra in (
        ("info",),
        ("check",),
        ("decode", "out"),
        ("units", "out"),
        ("patch", "translations", "out"),
    ):
        command = commands.add_parser(name)
        for argument in ("sec5", *extra):
            command.add_argument(argument, type=Path)
        if name == "patch":
            command.add_argument("--codepage", type=int)
    args = parser.parse_args()
    sec5 = Sec5(args.sec5.read_bytes())
    if args.command == "info":
        pages = struct.unpack_from("<4I", sec5.section[b"CHAR"])
        print("version", sec5.version, "code pages", *pages)
        for tag, data in sec5.sections:
            print(f"{tag.decode('ascii', 'replace')} {len(data)} bytes")
        for name, start in sec5.scripts:
            print(f"{start:#8x} {name.decode('cp932', 'replace')}")
    elif args.command == "check":
        return check(sec5)
    elif args.command == "decode":
        args.out.mkdir(parents=True, exist_ok=True)
        groups = {}
        for unit in sec5.units():
            groups.setdefault(unit.get("script") or "OPTN", []).append(unit["text"])
        for name, texts in groups.items():
            path = args.out / (name.replace("/", "_").replace("\\", "_") + ".txt")
            path.write_text("\n".join(texts) + "\n", encoding="utf-8", newline="\n")
    elif args.command == "units":
        text = json.dumps(sec5.units(), ensure_ascii=False, indent=1)
        args.out.write_text(text + "\n", encoding="utf-8", newline="\n")
    else:
        translations = json.loads(args.translations.read_text("utf-8-sig"))
        args.out.write_bytes(sec5.rebuild(translations, args.codepage))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SAS5Error as exc:
        sys.exit(str(exc))
