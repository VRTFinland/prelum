# Fonts and local packages

Prelum can receive a font with one render request or load shared resources mounted by the operator.
Choose based on who owns the resource and how often it is used:

| Resource | Best choice |
| --- | --- |
| A font used by one request or tenant | Send it in the request's `files` object |
| A font shared by many templates | Mount `PRELUM_FONT_PATH` read-only |
| A private, versioned Typst package | Mount `PRELUM_LOCAL_PACKAGE_PATH` read-only |

## Send a font with one request

Files are written into the temporary project before compilation, and the project root is passed to
Typst as `--font-path`. A request-provided `.ttf` or `.otf` file is therefore available by the family
name embedded in the font. Encode the font as Base64 in `files`, just like any other binary asset;
see [Add project files](../api/render.md#add-project-files).

The caller is responsible for having the right to use and transmit every supplied font. Prelum
does not inspect font licences or grant redistribution rights.

## Mount shared fonts

Set `PRELUM_FONT_PATH` to one additional recursively searched font directory. It must be an
existing, readable absolute path, must not contain the platform path-list separator and should be
mounted read-only. Request-provided fonts remain available alongside it.

```bash
docker run --rm \
  -e PRELUM_API_TOKEN=replace-me \
  -e PRELUM_FONT_PATH=/opt/prelum/fonts \
  -v "$PWD/fonts:/opt/prelum/fonts:ro" \
  ghcr.io/vrtfinland/prelum:VERSION
```

## Mount local Typst packages

`PRELUM_LOCAL_PACKAGE_PATH` exposes a read-only directory of versioned local packages. A package
stored under `local/acme-invoice/2.1.0/` is imported by the request's `source` as:

```typst
#import "@local/acme-invoice:2.1.0": render
#render(data)
```

Packages keep their own Typst root. They cannot read request files unless the inline source passes
a project path or loaded value to them. Configuring local packages neither enables downloads nor
adds a template-by-name render path.

Remote `@preview` packages are disabled by default. Enable them only when downloads are intentional;
see [Configuration](configuration.md) and [Security model](security-model.md).
