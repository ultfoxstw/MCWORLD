# Minecraft World Data Extractor

A production-quality Windows desktop application that extracts and reports on
Minecraft world archives without ever modifying the source.

## Supported input formats

The application detects the actual container from its **file signature**, not
just the extension:

- `.EPK` (with an internal ZIP payload)
- Vanilla Minecraft world `.zip`
- Bedrock `.mcworld`
- Extracted world folders (Java or Bedrock)

If a file's extension is wrong, we prefer the signature. If we cannot identify
the container, we refuse rather than guess.

## How format detection works

```
INPUT
  │
  ▼
FILE SIGNATURE DETECTOR
  ├──▶ EPK      (extension + ZIP scan of payload)
  ├──▶ ZIP      ("PK" local header)
  ├──▶ MCWORLD  (ZIP contents + .mcworld extension)
  ├──▶ DIRECTORY (contains level.dat or db/)
  └──▶ UNKNOWN  (never fabricated)
        │
        ▼
CONTAINER READER (read-only, temp extraction)
        │
        ▼
WORLD ROOT DETECTOR (searches for level.dat / db/)
        │
        ▼
WORLD FORMAT DETECTOR (Java: region/, playerdata/. Bedrock: db/)
        │
        ▼
NBT / LEVELDB PARSERS  ──▶  NORMALIZED DATASET
                                  │
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
               CLEAN EXPORT                RAW EXPORT
                    │                           │
                    └────────────┬──────────────┘
                                 ▼
                       UTF-8 VALIDATION
                                 ▼
                           DOWNLOADS/
```

## Selecting a world

- Click **Select .EPK / .ZIP / .MCWORLD** to pick an archive.
- Or **Select Folder** to pick an already-extracted world directory.

## Clean mode vs raw mode

There is **one** extraction pipeline. Raw and clean exports come from the same
parsed dataset — toggling the mode never re-parses the source.

- **Clean mode**: human-readable player, book, and named-mob reports.
- **Raw mode**: preserves the parsed NBT hierarchy as indented
  `Compound { Tag { Value } }` output. It never dumps binary bytes.

## Output locations

Reports are saved to your Windows **Downloads** folder as:

- `player_data.txt`
- `book_data.txt`
- `named_mobs_data.txt`

If a file already exists, the new file is suffixed with `_YYYY-MM-DD_HH-MM-SS`.

## Build locally

```
pip install -r requirements.txt
pip install pyinstaller
pyinstaller --noconfirm build.spec
```

The `.exe` will land in `dist/MinecraftWorldExtractor.exe`.

## Build through GitHub Actions

Push to `main`/`master`, or run the **Build Windows EXE** workflow manually.
The workflow:

1. Checks out the repo
2. Installs dependencies
3. Runs the pytest suite
4. Builds the `.exe` with PyInstaller
5. Uploads it as a downloadable artifact

## Known limitations

- Bedrock LevelDB records that are exclusively snappy-compressed and packed
  behind complex block-index encodings may be reported as "not decoded" if
  `cramjam` cannot decompress the specific block layout. All such misses are
  counted and reported — never silently dropped.
- The application does not attempt to decode block palettes (chunk terrain
  data). It focuses on players, books and named entities as requested.
- Very old (pre-1.7) alpha/beta chunk formats are not parsed.

## How incomplete / corrupt data is reported

Every extractor keeps two counters, e.g.:

```
Players discovered: 36
Players successfully parsed: 34
Books discovered: 17
Books successfully parsed: 16
Named mobs discovered: 9
Named mobs successfully parsed: 9
```

When *discovered != parsed*, the log lists specific warnings. If UTF-8
validation flags an output file, a `[OUTPUT VALIDATION FAILED - see warnings]`
banner is added so the failure is impossible to miss.

## Read-only guarantee

- The source file is opened in read-only mode.
- ZIP / MCWORLD / EPK archives are extracted into a private temp directory
  which is deleted at the end of each extraction run.
- Path-traversal attempts inside archives are blocked.
- The application never writes to region files, player data, databases or
  NBT structures inside the source world.

## Project layout

```
src/
  format_detection/  # magic-byte detector
  containers/        # zip/mcworld/epk/folder readers, temp extraction
  nbt/               # tolerant NBT parser (big/little, gzip/zlib/raw)
  java/              # level.dat, playerdata, region .mca readers
  bedrock/           # LevelDB best-effort scanner
  extractors/        # single-source-of-truth data model
  exporters/         # clean + raw formatters, UTF-8 validation
  gui/               # Tkinter GUI (non-freezing, async)
  pipeline.py        # orchestrator
  main.py            # entry point (CLI + GUI)
tests/               # pytest suite
.github/workflows/   # Windows CI + artifact upload
build.spec           # PyInstaller spec
```
