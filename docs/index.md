---
hide:
  - navigation
  - toc
---

<section class="prelum-hero">
  <div class="prelum-hero__copy">
    <div class="prelum-hero__brand">
      <img class="prelum-hero__logo" src="prelum-logo-concept-white.svg" alt="">
      <span>Prelum</span>
    </div>
    <p class="prelum-hero__eyebrow">Independent HTTP service for Typst</p>
    <h1>Run Typst behind a production-ready API.</h1>
    <p class="prelum-hero__lead">
      Prelum is an open-source service by <a href="https://gisgro.com">GISGRO Oy</a>. Send Typst source, assets and data;
      receive PDF, SVG or PNG through one versioned HTTP API.
    </p>
    <div class="prelum-hero__actions">
      <a href="getting-started/" class="md-button md-button--primary">Get started</a>
      <a href="api/render/" class="md-button">Explore the API</a>
    </div>
  </div>
  <div class="prelum-hero__request" aria-label="Example render request">
    <div class="prelum-hero__request-header">
      <span>POST</span>
      <code>/v1/render</code>
    </div>
    <pre><code>{
  "source": "= Hello from Prelum",
  "output": {
    "format": "pdf"
  }
}</code></pre>
  </div>
</section>

## Typst is the typesetting system. Prelum is the service around it.

[Typst](https://typst.app/open-source/) is the open-source markup language and compiler developed
by [Typst GmbH](https://typst.app/legal/). It owns the document syntax, layout engine and output
formats. Prelum is an independent [GISGRO Oy](https://gisgro.com) project that runs the compiler
and adds a versioned HTTP API, authentication, resource limits and a container-ready operational
boundary.

In this documentation, a **Typst project** means the input to one compilation: the required Typst
`source` plus any request-provided `files` and `data`. Prelum does not store these projects or
resolve templates by name; every render request is self-contained.

## Why Typst for document generation?

A browser can also generate a PDF and is often the right choice when the document is already a web
page. Typst starts from a different premise: the page—not the viewport—is the primary output.

<div class="grid cards" markdown>

-   **Precise, repeatable pages**

    Typst lays out content in physical page coordinates. With a pinned Prelum image and controlled
    fonts and packages, the same source and data produce the same pagination and page geometry
    within that runtime, without depending on browser navigation or page-load timing.

    [Explore Typst's page model →](https://typst.app/docs/reference/layout/page/)

-   **Document logic in one language**

    Lightweight markup, reusable styles and scripting work together. Data-driven reports do not
    need a separate DOM, print stylesheet and client-side JavaScript layer.

    [Read about syntax and scripting →](https://typst.app/docs/reference/syntax/)

-   **PDF requirements are first-class**

    Typst generates tagged PDFs by default and supports selectable PDF versions, archival PDF/A
    profiles and PDF/UA-1 checks. Vector PDF preserves page geometry at any zoom; when fixed raster
    dimensions are required, Prelum provides explicit
    [PNG PPI](api/output-formats.md#direct-png-and-svg).

    [See Typst's PDF support →](https://typst.app/docs/reference/pdf/)

</div>

!!! tip "When HTML-to-PDF is the better fit"

    Choose browser-based generation when the source of truth is already HTML, the PDF must closely
    match an existing web view, or the template depends on browser-specific CSS or JavaScript.
    Choose Typst when the document itself is the product: reports, invoices, certificates, forms
    and other repeatable page-based output.

## What it provides

- one strict, versioned `POST /v1/render` API;
- PDF versions, PDF/A and PDF/UA conformance options;
- direct PNG and SVG output or bounded multi-page ZIP archives;
- request-local images, fonts and modules;
- read-only operator-mounted fonts and local packages;
- bounded request, render, queue and output resources;
- a non-root, multi-platform container image with SBOM, provenance and a Cosign signature.

## Quick example

Start the service with its published development token:

```bash
docker build -t prelum:latest .
docker run --rm -p 9870:9870 \
  -e PRELUM_ENVIRONMENT=development \
  prelum:latest
```

Render the checked-in example:

```bash
curl -X POST http://localhost:9870/v1/render \
  -H "Content-Type: application/json" \
  -H "X-Prelum-Api-Token: dev-only-insecure-token" \
  -d @examples/render-request.json \
  --output hello.pdf
```

Continue with [Getting started](getting-started.md), then see the
[render request](api/render.md) and [output formats](api/output-formats.md).

!!! warning "Caller-supplied programs"

    A render request contains Typst source that reaches the compiler. Read the
    [security model](operations/security-model.md) before exposing Prelum outside a trusted
    network.
