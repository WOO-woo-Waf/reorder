# Archive Identification Plan

## Current Behavior

The current beta pipeline intentionally uses a broad extraction strategy.

Archive detection is mainly implemented in `ArchiveSignatureInspector` and nested candidate collection:

- Name-based archive match:
  - `.zip`, `.rar`, `.7z`, `.tar`, `.gz`, `.bz2`, `.xz`, `.tgz`, `.tar.gz`
  - split volumes such as `.7z.001`, `.zip.001`, `.001`, `.z01`, `.r00`
  - part volumes such as `.part1.rar`
- Header-based archive match:
  - ZIP: `50 4B 03 04`
  - 7z: `37 7A BC AF 27 1C`
  - RAR4: `52 61 72 21 1A 07 00`
  - RAR5: `52 61 72 21 1A 07 01 00`
  - GZip: `1F 8B 08`
- Apate probe:
  - Only considered Apate when the stored original head restores to a known archive signature.
- Embedded archive-name trim:
  - Names like `xxx.7z删除` can be trimmed to `xxx.7z`.
- Unknown suffix variants:
  - Some unknown files are renamed temporarily to `.rar`, `.zip`, `.7z` and tried.
- Deep extraction fallback:
  - If an extracted directory contains one direct file and that file is large enough, it can be treated as a candidate even when it does not look like an archive.
  - This is why real videos can be tried as archives after being extracted.

This behavior is useful for disguised resources, but it is intentionally noisy.

## Why Videos And Images Get Tried

The broad part is this rule:

If an extraction layer produces exactly one file and no subdirectories, and the file is archive-like or larger than the configured threshold, the pipeline returns it as a nested candidate.

That means a large `.mp4` can be retried because it is a large single output. This matches the older requirement: "large media may actually be disguised archives." It is not precise enough for normal media payloads.

## Media File Headers

Many media formats do have stable signatures or container markers. They are usually easier to reject than archive formats are to prove.

Common image signatures:

- JPEG: starts with `FF D8 FF`
- PNG: starts with `89 50 4E 47 0D 0A 1A 0A`
- GIF: starts with `GIF87a` or `GIF89a`
- WebP: starts with `RIFF....WEBP`
- BMP: starts with `42 4D`

Common video/audio container markers:

- MP4/MOV: usually has `ftyp` at byte offset 4, with brands such as `isom`, `mp42`, `qt  `
- MKV/WebM: starts with EBML `1A 45 DF A3`
- AVI: starts with `RIFF....AVI `
- WAV: starts with `RIFF....WAVE`
- MP3: starts with `ID3` or frame sync `FF FB` / `FF F3` / `FF F2`
- FLAC: starts with `fLaC`
- OGG: starts with `OggS`

These signatures are not perfect, but they are strong enough for a conservative "do not deep-extract normal media" gate.

## Proposed Precision Model

Use a tiered confidence score instead of a yes/no archive guess.

### High Confidence Archive

Try extraction normally:

- Known archive header at offset 0.
- Known split-volume naming where the entry volume is present.
- Apate probe restores to a known archive header.
- Name has an embedded archive suffix and trim would produce a known archive name.

### Medium Confidence Archive

Try extraction, but only in controlled cases:

- Unknown file with media-looking suffix but no valid media signature.
- Known disguised patterns from `docs/todo.md`, such as `.jpg/.exe -> .rar/.zip/.7z`.
- Large unknown single file with no media signature and no obvious final payload context.

### Low Confidence / Final Payload

Do not deep-extract by default:

- Valid media header and media suffix match.
- Extracted layer contains multiple normal media files.
- Single large file has valid media header and no archive signature.

This would stop the common case where a real `.mp4` gets treated as another archive.

## Recommended Pipeline Changes

1. Add `detect_media_suffix(path)` beside `detect_archive_suffix(path)`.

2. Add a probe result field or helper for `kind=MEDIA`.

3. Update nested candidate collection:

   - If file is a valid media file, do not return it as a nested candidate.
   - Exception: allow force mode for known disguise recipes or explicit user option.

4. Replace the large single-file fallback:

   Current:

   - one big file -> candidate

   Better:

   - one big file with archive signature -> candidate
   - one big file with Apate archive restore -> candidate
   - one big file with media signature -> final payload
   - one big file unknown -> candidate only if `deep_unknown_single=true`

5. Add config switches:

   - `beta.deep_extract.try_large_unknown_single`: default `true` for compatibility, later can become `false`
   - `beta.deep_extract.skip_valid_media`: default `true`
   - `beta.deep_extract.force_disguised_media_suffixes`: default `[".jpg", ".jpeg", ".exe"]`; keep `.mp4` optional

6. Log why a file is skipped:

   - `SKIP-NESTED: file=xxx.mp4 kind=media reason=valid-mp4`
   - This keeps the behavior explainable.

## Password Deduplication

Password deduplication is already implemented in `PasswordRepository`.

Current behavior:

- Read the password file as UTF-8 with ignored decoding errors.
- Strip each line.
- Skip empty lines.
- Skip lines beginning with `#`.
- Keep the first occurrence of each exact password.
- Preserve order.

Example:

```text
123
abc
123
```

Loads as:

```text
123
abc
```

Possible future improvement:

- Optionally support inline comments, such as `123 # note`.
- Optionally normalize full-width spaces.
- Log both raw count and deduplicated count for easier auditing.

