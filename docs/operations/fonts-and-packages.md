# Fonts and local packages

## Request-provided fonts

Files are written into the temporary project before compilation, and the project root is passed to
Typst as `--font-path`. A request-provided `.ttf` or `.otf` file is therefore available by the family
name embedded in the font.

The caller is responsible for having the right to use and transmit every supplied font. Prelum
does not inspect font licences or grant redistribution rights.

## Mounted fonts

Set `PRELUM_FONT_PATH` to one additional recursively searched font directory. It must be an
existing, readable absolute path, must not contain the platform path-list separator and should be
mounted read-only. Request-provided fonts remain available alongside it.

```bash
docker run --rm \
  -e PRELUM_API_TOKEN=replace-me \
  -e PRELUM_FONT_PATH=/opt/prelum/fonts \
  -v "$PWD/fonts:/opt/prelum/fonts:ro" \
  ghcr.io/vrtfinland/prelum:<tag>
```

## Local Typst packages

`PRELUM_LOCAL_PACKAGE_PATH` exposes a read-only directory of versioned local packages. A package
stored under `local/acme-invoice/2.1.0/` is imported by required inline source as:

```typst
#import "@local/acme-invoice:2.1.0": render
#render(data)
```

Packages keep their own Typst root. They cannot read request files unless the inline source passes
a project path or loaded value to them. Configuring local packages neither enables downloads nor
adds a template-by-name render path.
