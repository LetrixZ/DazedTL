"""Len's SAS5 tools, on small files built here."""

import importlib.util
import struct
import unittest
from pathlib import Path

from PIL import Image

TOOLS = (
    Path(__file__).resolve().parents[1]
    / "backend/dazedtl/engine/data/skills/game-translation/tools/C++/SAS5"
)


def tool(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


text, iar, exe = tool("sas5_text"), tool("sas5_iar"), tool("sas5_exe")
LINE = "あ".encode("cp932")


def u32(value):
    return struct.pack("<I", value)


def command(data):
    return b"\x1b\x02\x00\x01" + struct.pack("<H", len(data)) + data + b"\xff"


def sec5(direct=None):
    """A file and its library's offset: a jump to the library, a string, a
    message, a relative address of it, then a library script that calls the
    string's command and is the exported handler."""

    def code(target):
        first = command(b"\x70" + u32(target) + b"\xff")
        string = command(b"\x7a\x00" + bytes([len(LINE)]) + LINE + b"\xff")
        message = len(first) + len(string)
        head = first + string + LINE + text.BREAK + LINE
        operand_end = len(head) + 6 + 1 + 4
        head += command(b"\x12" + u32((message - operand_end) & 0xFFFFFFFF) + b"\xff")
        return head + b"\x0e" + u32(len(first)) + b"\x11", len(head)

    library = code(0)[1]
    data = code(library if direct is None else direct)[0]
    option = b"\x00" + u32(2)
    for name, value in (
        (b"EXPORT_FUNS", b"\x00" + u32(1) + u32(1) + b"f\x02" + u32(library)),
        (b"Title", b"\x03" + u32(len(LINE)) + LINE),
    ):
        option += u32(len(name)) + name + value
    czit = u32(2) + b"a.sal\0" + u32(0) + b"lib.sal\0" + u32(library)
    out = b"SEC5" + u32(110000)
    for tag, body in (
        (b"CHAR", bytes(16)),
        (b"OPTN", option),
        (b"CZIT", czit),
        (b"DTDE", u32(0)),
        (b"CODE", text.cipher(data, encode=True)),
        (b"ENDS", b""),
    ):
        out += tag + u32(len(body)) + body
    return out, library


class Sas5TextTests(unittest.TestCase):
    def test_longer_text_moves_every_address(self):
        raw, library = sec5()
        old = text.Sec5(raw)
        self.assertEqual(old.rebuild({}), raw)
        units = {unit["kind"]: unit["id"] for unit in old.units()}
        translations = {
            units["message"]: "い" * 20 + "\n" + "う" * 9 + "\n" + "え",
            units["string"]: "お" * 30,
            "OPTN/Title": "Longer title",
        }
        new = text.Sec5(old.rebuild(translations, 932))
        self.assertEqual(new.problems(), [])
        moved = new.scripts[1][1]
        self.assertGreater(moved, library)
        self.assertEqual(new.exports(), [("/EXPORT_FUNS/f", moved)])
        self.assertEqual(
            [new.code.target(*row) for row in new.code.addresses],
            [moved, new.code.texts[0][0], old.code.target(*old.code.addresses[2])],
        )
        self.assertEqual(new.codepage, 932)
        found = {unit["kind"]: unit["text"] for unit in new.units()}
        self.assertEqual(found["string"], translations[units["string"]])
        self.assertEqual(found["message"], translations[units["message"]])
        # The installed, patched file has its addresses moved, so building
        # from it refuses, naming the original to build from.
        with self.assertRaisesRegex(text.SAS5Error, "Build from the original"):
            new.rebuild(translations, 932)

    def test_older_files_jump_through_19_with_wider_lengths(self):
        head = b"\x1b\x02\x00\x01" + u32(6) + b"\x19" + u32(0) + b"\xff\xff" + LINE
        end = len(head)
        data = head.replace(b"\x19" + u32(0), b"\x19" + u32(end)) + b"\x1b\x04\x00\xff"
        out = b"SEC5" + u32(107000)
        for tag, body in (
            (b"CHAR", bytes(16)),
            (b"OPTN", b"\x00" + u32(0)),
            (b"CZIT", u32(1) + b"a.sal\0" + u32(0)),
            (b"DTDE", u32(0)),
            (b"CODE", text.cipher(data, encode=True)),
        ):
            out += tag + u32(len(body)) + body
        old = text.Sec5(out)
        self.assertEqual(old.rebuild({}), out)
        new = text.Sec5(old.rebuild({old.units()[0]["id"]: "い" * 9}))
        self.assertEqual(new.code.target(*new.code.addresses[0]), end + 16)

    def test_refuses_what_the_engine_could_not_read(self):
        with self.assertRaisesRegex(text.SAS5Error, "Unmapped"):
            text.Sec5(sec5(direct=3)[0]).rebuild({})
        old = text.Sec5(sec5()[0])
        string = next(u["id"] for u in old.units() if u["kind"] == "string")
        for bad in ("お" * 128, "a\x1b", "\U0001f600"):
            with self.assertRaises(text.SAS5Error):
                old.rebuild({string: bad})


class Sas5IarTests(unittest.TestCase):
    def archive(self):
        entries = []
        for flags, depth in ((0x02, 1), (0x1C, 3), (0x3C, 4)):
            stride = (5 * depth + 3) // 4 * 4
            size = stride * 3
            head = struct.pack("<HBBIIIII", flags, 0, 0, 0, size, 0, size, 0)
            head += struct.pack("<iiIII", -4, 7, 5, 3, stride)
            entries.append(head.ljust(0x48, b"\x05") + bytes(size))
        offsets, at = b"", 0x20 + 8 * len(entries)
        for entry in entries:
            offsets += struct.pack("<Q", at)
            at += len(entry)
        head = b"iar " + struct.pack("<HH", 4, 1) + bytes(16) + u32(3) + u32(3)
        return head + offsets + b"".join(entries)

    def test_packs_each_depth_and_keeps_other_entries(self):
        old = iar.Archive(self.archive())
        for index, (mode, depth) in enumerate((("L", 1), ("RGB", 3), ("RGBA", 4))):
            picture = Image.frombytes(mode, (5, 3), bytes(range(15 * depth)))
            new = iar.Archive(old.packed({index: picture}))
            changed = new.entries[index]
            self.assertEqual(changed.image().tobytes(), picture.tobytes())
            self.assertEqual((changed.x, changed.y), (-4, 7))
            for other in {0, 1, 2} - {index}:
                self.assertEqual(new.entries[other].data, old.entries[other].data)
        with self.assertRaises(iar.IarError):
            old.packed({2: Image.new("RGBA", (6, 3))})

    def test_compression_reads_back_at_every_offset_and_length_code(self):
        noise, seed = bytearray(), 1
        for _ in range(0x2000):
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            noise.append(seed >> 16 & 0xFF)
        for offset in (1, 0x200, 0x201, 0x401, 0x801, 0x1000, 0x1001, 0x2000):
            data = bytes(noise[:offset])
            for length in (3, 6, 7, 8, 9, 16, 17, 272):
                data += (data[-offset:] * (length // offset + 1))[:length] + b"\0\xff"
            self.assertEqual(iar.decompress(iar.compress(data), len(data)), data)
        # A stream by hand: two literals, then each kind of match, so that an
        # encoder and decoder wrong alike still fail.
        # Bits, low first: 1 1 | 0 1 0 1 1 (offset 2, 3 bytes) | 0 0 0 (2 bytes).
        bits = [1, 1, 0, 1, 0, 1, 1, 0, 0, 0]
        stream = struct.pack("<H", sum(bit << n for n, bit in enumerate(bits)))
        stream += b"ab" + b"\x01" + b"\x00"
        self.assertEqual(iar.decompress(stream, 7), b"ababa" + b"aa")
        with self.assertRaises(iar.IarError):
            iar.decompress(stream, 50)


def _pe_with_ui():
    """Minimal valid 32-bit PE with .rdata (UTF-16LE, cp932, and ASCII/pointer
    noise that must be ignored) and .rsrc (GMO gettext catalog, RT_STRING,
    RT_MENU, RT_DIALOG)."""
    file_align, sec_align = 0x200, 0x1000
    # Build .rdata payload: one UTF-16LE Japanese UI string, one cp932 one,
    # plus ASCII string and 32-bit pointer noise that must not be extracted.
    utf16_str = "セーブ確認".encode("utf-16-le") + b"\x00\x00"
    cp932_str = "設定を保存します。".encode("cp932") + b"\x00"
    ascii_noise = b"MainWindowPosX\x00\x00"
    ptr_noise = struct.pack("<3H", 0x5078, 0x006E, 0x0020) + b"\x00\x00"
    rdata_body = (
        b"\x00\x00\x00\x00"
        + utf16_str
        + b"\x00\x00"
        + cp932_str
        + b"\x00\x00"
        + ascii_noise
        + ptr_noise
    )
    rdata_raw = rdata_body.ljust(file_align, b"\x00")

    # Build SAS5 GMO (GNU gettext .mo) catalog + RT_STRING + RT_MENU + RT_DIALOG.
    gmo_parsed = {
        "rev": 0,
        "hash_size": 7,
        "encoding": "cp932",
        "origs": [b"", b"Config", b"Save"],
        "trans": [
            b"Content-Type: text/plain; charset=Shift_JIS\n",
            "ユーザー設定".encode("cp932"),
            "セーブ".encode("cp932"),
        ],
    }
    rsrc_gmo = exe._build_mo(gmo_parsed, {})
    slots = [""] * 16
    slots[1] = "保存しますか？"
    rsrc_string = exe._build_string_block(slots)
    rsrc_menu = (
        struct.pack("<HH", 0, 0)
        + struct.pack("<HH", 0x0000, 100)
        + "セーブ".encode("utf-16-le")
        + b"\x00\x00"
        + struct.pack("<HH", 0x0080, 101)
        + "終了".encode("utf-16-le")
        + b"\x00\x00"
    )
    rsrc_dialog = (
        struct.pack("<IIHHHHH", 0, 0, 1, 0, 0, 100, 50)
        + b"\x00\x00"  # menu
        + b"\x00\x00"  # class
        + "環境設定".encode("utf-16-le")
        + b"\x00\x00"
    )
    while len(rsrc_dialog) % 4:
        rsrc_dialog += b"\x00"
    rsrc_dialog += (
        struct.pack("<IIHHHHH", 0, 0, 10, 10, 40, 12, 1)
        + struct.pack("<HH", 0xFFFF, 0x0080)  # Button class
        + "決定".encode("utf-16-le")
        + b"\x00\x00"
        + struct.pack("<H", 0)
    )
    tree = {
        (True, "GMO"): {(True, "ID_GMO_SAS5"): {(False, 0x0411): (rsrc_gmo, 0, 0)}},
        (False, exe.RT_MENU): {(False, 10): {(False, 0x0411): (rsrc_menu, 0, 0)}},
        (False, exe.RT_DIALOG): {(False, 20): {(False, 0x0411): (rsrc_dialog, 0, 0)}},
        (False, exe.RT_STRING): {(False, 1): {(False, 0x0411): (rsrc_string, 0, 0)}},
    }
    # Build minimal PE header first so PeFile._serialize_resources can run.
    pe_off = 0x80
    opt_size = 0xE0
    rsrc_vaddr = 0x2000
    dummy = object.__new__(exe.PeFile)
    rsrc_blob = dummy._serialize_resources(tree, rsrc_vaddr)
    rsrc_raw = rsrc_blob.ljust(file_align, b"\x00")

    dos = bytearray(pe_off)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, pe_off)
    coff = struct.pack("<HHIIIHH", 0x014C, 2, 0, 0, 0, opt_size, 0x0102)
    opt = bytearray(opt_size)
    struct.pack_into("<H", opt, 0, 0x10B)  # PE32
    struct.pack_into("<II", opt, 32, sec_align, file_align)
    struct.pack_into("<II", opt, 56, 0x3000, file_align)
    struct.pack_into("<I", opt, 92, 16)
    struct.pack_into("<II", opt, 96 + 16, rsrc_vaddr, len(rsrc_blob))
    sec1 = struct.pack(
        "<8sIIIIIIHHI",
        b".rdata\x00\x00",
        len(rdata_body),
        0x1000,
        len(rdata_raw),
        file_align,
        0,
        0,
        0,
        0,
        0x40000040,
    )
    sec2 = struct.pack(
        "<8sIIIIIIHHI",
        b".rsrc\x00\x00\x00",
        len(rsrc_blob),
        rsrc_vaddr,
        len(rsrc_raw),
        file_align + len(rdata_raw),
        0,
        0,
        0,
        0,
        0x40000040,
    )
    headers = (bytes(dos) + b"PE\0\0" + coff + bytes(opt) + sec1 + sec2).ljust(
        file_align, b"\x00"
    )
    return headers + rdata_raw + rsrc_raw


class Sas5ExeTests(unittest.TestCase):
    def test_extracts_and_rebuilds_resources_and_static_ui_strings(self):
        raw = _pe_with_ui()
        pe = exe.PeFile(raw)
        self.assertEqual(pe.check(), [])
        units = {unit["id"]: unit for unit in pe.units()}
        self.assertEqual(units["RSRC/GMO/ID_GMO_SAS5/1"]["msgid"], "Config")
        self.assertEqual(units["RSRC/GMO/ID_GMO_SAS5/1"]["text"], "ユーザー設定")
        self.assertEqual(units["RSRC/GMO/ID_GMO_SAS5/2"]["text"], "セーブ")
        self.assertEqual(units["RSRC/STRING/1"]["text"], "保存しますか？")
        self.assertEqual(units["RSRC/MENU/10/0"]["text"], "セーブ")
        self.assertEqual(units["RSRC/MENU/10/1"]["text"], "終了")
        self.assertEqual(units["RSRC/DIALOG/20/title"]["text"], "環境設定")
        self.assertEqual(units["RSRC/DIALOG/20/0"]["text"], "決定")
        # ASCII and pointer-table noise in .rdata are not extracted.
        utf16_ids = [uid for uid in units if uid.startswith("UTF16/")]
        cp932_ids = [uid for uid in units if uid.startswith("CP932/")]
        self.assertEqual(len(utf16_ids), 1)
        self.assertEqual(len(cp932_ids), 1)
        utf16_id, cp932_id = utf16_ids[0], cp932_ids[0]
        self.assertEqual(units[utf16_id]["text"], "セーブ確認")
        self.assertEqual(units[utf16_id]["max_characters"], 5)
        self.assertEqual(units[cp932_id]["text"], "設定を保存します。")
        self.assertEqual(units[cp932_id]["max_bytes"], 18)

        # Long English translations force .rsrc expansion into a new .rsrc2 section.
        long_prompt = "Do you really want to overwrite this save slot? " * 8
        rebuilt = pe.rebuild(
            {
                "RSRC/GMO/ID_GMO_SAS5/1": "User Settings",
                "RSRC/GMO/ID_GMO_SAS5/2": "Save Game",
                "RSRC/STRING/1": long_prompt,
                "RSRC/MENU/10/0": "Save Game",
                "RSRC/MENU/10/1": "Quit",
                "RSRC/DIALOG/20/title": "System Settings",
                "RSRC/DIALOG/20/0": "Apply",
                utf16_id: "Save?",
                cp932_id: "Save settings.",
            }
        )
        after = exe.PeFile(rebuilt)
        self.assertEqual(after.check(), [])
        self.assertEqual(after.num_sections, 3)
        self.assertEqual(after.sections[-1]["name"], b".rsrc2")
        # Verify rebuilt GMO catalog keeps msgids and stores English translations.
        gblob, _cp, _res = after.resources[(True, "GMO")][(True, "ID_GMO_SAS5")][
            (False, 0x0411)
        ]
        parsed_mo = exe._parse_mo(gblob)
        self.assertEqual(parsed_mo["origs"][1:], [b"Config", b"Save"])
        self.assertEqual(parsed_mo["trans"][1:], [b"User Settings", b"Save Game"])
        # Verify rebuilt Win32 resources decode back with the English labels.
        blob, _cp, _res = after.resources[(False, exe.RT_STRING)][(False, 1)][
            (False, 0x0411)
        ]
        self.assertEqual(exe._parse_string_block(blob, 1)[1], long_prompt)
        mblob, _cp, _res = after.resources[(False, exe.RT_MENU)][(False, 10)][
            (False, 0x0411)
        ]
        self.assertEqual(
            [item[2] for item in exe._parse_menu(mblob)[2]], ["Save Game", "Quit"]
        )
        dblob, _cp, _res = after.resources[(False, exe.RT_DIALOG)][(False, 20)][
            (False, 0x0411)
        ]
        parsed_dlg = exe._parse_dialog(dblob)
        self.assertEqual(parsed_dlg[4], "System Settings")
        self.assertEqual(parsed_dlg[6][0][2], ("str", "Apply"))
        # In-place static strings were patched at their offsets.
        off16 = int(utf16_id.split("/")[1], 16)
        self.assertEqual(rebuilt[off16 : off16 + 10], "Save?".encode("utf-16-le"))
        off932 = int(cp932_id.split("/")[1], 16)
        self.assertEqual(
            rebuilt[off932 : off932 + 18], b"Save settings.\x00\x00\x00\x00"
        )
        # Overlong static strings and already-patched inputs are refused.
        with self.assertRaisesRegex(exe.ExeError, "fit in place"):
            pe.rebuild({utf16_id: "Save Confirmation"})
        with self.assertRaisesRegex(exe.ExeError, "Build from the original"):
            after.rebuild({utf16_id: "Save?"})


if __name__ == "__main__":
    unittest.main()
