// A minimal template showing what a caller sends as `source`.
//
// The renderer binds the request's `data` object to a Typst variable called `data` in a prelude it
// prepends to this file, and writes any `files` entries alongside it — so `lib/label.typ`
// below is imported by the same relative path used as its key in the request.

#import "lib/label.typ": caption

#set page(paper: "a4", margin: 2cm)
#set text(size: 11pt)

= #caption(data.name)

Rendered by Prelum on request.
