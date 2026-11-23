# SAS5 (`*.sec5`, `*.iar`, `*.war`)

**Indicators:** a `.sec5` file starting `SEC5`, beside `.iar` images and `.war` audio; the exe's description is often `solfasys`.

**Tools:** `tools/C++/SAS5/sas5_text.py` and `sas5_iar.py`; run them with `--help`.
The sec5 version is the uint32 at offset 4.

| sec5 version | Text tool |
|---|---|
| 100000-104000 | SAS5OldScriptSimpleTool; its repacker leaves relative jumps alone, so longer text can jump wrongly |
| 107000, 108000, 109000, 109002, 110000 | `sas5_text.py`: a rebuilt 107000 game ran; 110000 was checked as a file only, the others not at all |

`tools/THIRD-PARTY.md` lists the external tools.

## Find all the text first

| Where | What | How |
|---|---|---|
| `CODE` | messages, nameplates, choices, menus, font names | `sas5_text.py units` |
| `OPTN` | Settings tabs, audio classes, setting captions, title | `sas5_text.py units` |
| the exe | Settings labels, save/load, backlog, confirmations, errors, as cp932 strings | search the exe; patching it is a per-game step |
| `.iar` | title, buttons, toolbar, logos | `sas5_iar.py` |
| HTML, readme | manual and album pages, often cp932 | edit as text |

Run `sas5_text.py decode` for `census --decoded`.
The census asks for a dump of `.sec5` but doesn't read the exe, so inventory its strings yourself.
A message's `context` holds the nameplate set before it; a string's `type` tells nameplates and choices from identifiers.
Never translate command names, resource names, `Registry`, `GlobalAppId`, `Context*` or the keys in `VirtualFontMappings`: they can be lookup names or identify saves.

## The container

`SEC5`, uint32 version, then sections of tag, uint32 length and data until `ENDS`.

- `CHAR`: four uint32 code pages (file reading, executor, expression, reserved).
- `CODE`: scripts and library code under a rolling XOR: `out = byte ^ k; k = (k + stored byte + 0x12) & 0xFF`.
- `CZIT`: each script's name and offset in `CODE`.
- `DTDE`: types, whose sizes are needed to read some operands.
- `OPTN`: a typed tree: 0 map, 1 tuple, 2 integer, 3 string, 4 resource, 5 label, 6 binary.
- `RES2`, or `RESR` in older files: resource names with their archive and index.

A command is `1B`, an int16 executor, arguments, `FF`.
An argument is an ID, a length (int16 from 109000, int32 before) and operations.
`1B F8 01 FF` breaks a line.
A `7A` string is a zero byte, a **uint8** length and the bytes, so it holds at most 255.

## Every address must move

The engine doesn't limit a message's length, but changing one moves everything after it.
Rewrite all of these:

- direct operands `08`, `0B`, `0E`, `70`, and relative ones `09`, `0C`, `0F`, `12` (from the operand's end), also inside longer expressions and library scripts that hold no text;
- before 109000, every `19` operand too: jumps and calls use it (from 109000 it is a plain number);
- argument lengths and `CZIT`;
- `OPTN/EXPORT_FUNS`, the engine's event handlers, and OPTN labels.

A build that misses some can start and fail later: stale `EXPORT_FUNS` entries send pause, key, mouse and screen events into unrelated bytes, so check them first when Settings or menus break.
Never search `CODE` for values that look like addresses, and never parse the translated file for text; build from the original.
`sas5_text.py check` must report an identical rebuild and no unmapped address before translation starts.

## Locale

Code page 0 in `CHAR` means the system's, so Japanese text turns to mojibake without a Japanese locale.
On a 107000 game under code page 1252, `patch --codepage 932` only turned that mojibake into `?`, since the display still went through the system's code page; don't count on it.
ASCII text showed correctly with `CHAR` unchanged, so make all shown text ASCII: `OPTN` titles and setting captions, `―`, full-width marks and U+3000.
The exe's own strings (Settings labels) showed as `?` in the untouched game too; only patching the exe or a Japanese locale fixes those.

## Images

**Never carve an `.iar` for JPEG or PNG signatures**: entries are LZ-compressed bitmaps, and such matches are noise that won't open.
Look at several unpacked pictures before listing them for Images.

- Header: `iar `, uint16 version 1-4, uint32 counts at 0x18 and 0x1C, offsets from 0x20 (uint32 to version 2, then uint64). There is no checksum.
- Entry: uint16 flags, compressed byte at 3, unpacked, palette and packed sizes from 8, x and y at 0x18, width, height and stride from 0x20. Its header is 0x30, 0x40, 0x40 or 0x48 bytes by version.
- `flags & 0x3E`: 0x02 is 8-bit, 0x1C BGR, 0x3C BGRA.
- `0x1000` entries are layer lists and `0x800` ones differences from a base entry. `sas5_iar.py` lists both without unpacking them; use SAS5Tool's `ArcTool`.

`unpack --sec5` names entries from `RES2` or `RESR`.
Edit every state of a button, keep each picture's size and mode, and pack only changed ones.

## Check in the game

Startup alone proves little.
Test a new game, choices, right click, Settings and each of its tabs, save and load, backlog, auto and skip, every edited button state, and quit, with and without a Japanese locale.
Record what wasn't tested.
