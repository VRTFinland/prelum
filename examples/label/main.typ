// An 80x50 mm asset label. Page size belongs to the template, not to the request: the same
// endpoint returns this and the A4 invoice, and only the source decides which.

#set page(width: 80mm, height: 50mm, margin: 5mm)
#set text(size: 8pt)

#text(size: 13pt, weight: "bold")[#data.asset]

#v(1mm)
#line(length: 100%, stroke: 0.5pt)
#v(1mm)

#grid(
  columns: (auto, 1fr),
  row-gutter: 1.6mm,
  column-gutter: 3mm,
  [Site], [#data.site],
  [Inspected], [#data.inspected],
  [Next due], [*#data.next_due*],
  [Inspector], [#data.inspector],
)
