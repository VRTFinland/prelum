// A multi-page report. Each section starts a page, so the document exercises page selection and
// ZIP archive output as well as ordinary single-file PDF output.

#set page(paper: "a4", margin: 2.5cm, numbering: "1 / 1")
#set text(size: 11pt)
#set par(justify: true)

#align(center)[
  #text(size: 18pt, weight: "bold")[#data.title] \
  #v(2mm)
  #data.site — #data.period
]

#for (index, section) in data.sections.enumerate() [
  #if index > 0 [#pagebreak()] else [#v(1cm)]
  == #section.heading
  #section.body
]
