// An A4 invoice. Everything variable comes from the request's `data` object, which the renderer
// binds to `data` in a prelude it prepends to this file; `data.json` beside this template is the
// example payload.

#let currency(amount) = {
  let cents = calc.round(amount * 100)
  let minor = calc.rem-euclid(cents, 100)
  [#calc.div-euclid(cents, 100).#if minor < 10 [0#minor] else [#minor] #data.currency]
}

#let net = data.lines.map(line => line.quantity * line.unit_price).sum()
#let vat = net * data.vat_rate
#let total = net + vat

#set page(paper: "a4", margin: 2cm, numbering: none)
#set text(size: 10pt, font: "Libertinus Serif")

#grid(
  columns: (1fr, 1fr),
  align: (left, right),
  [
    #text(size: 16pt, weight: "bold")[#data.seller.name] \
    #data.seller.address.join("\n") \
    VAT #data.seller.vat
  ],
  [
    #text(size: 20pt, weight: "bold")[Invoice] \
    #data.number \
    Issued #data.date \
    Due #data.due
  ],
)

#v(1.5cm)

*Bill to* \
#data.customer.name \
#data.customer.address.join("\n")

#v(1cm)

#table(
  columns: (1fr, auto, auto, auto),
  align: (left, right, right, right),
  stroke: (x, y) => if y == 0 { (bottom: 0.6pt) } else { none },
  inset: 8pt,
  table.header[*Description*][*Quantity*][*Unit price*][*Amount*],
  ..data.lines
    .map(line => (
      line.description,
      [#line.quantity #line.unit],
      currency(line.unit_price),
      currency(line.quantity * line.unit_price),
    ))
    .flatten(),
)

#v(0.5cm)

#align(right)[
  #table(
    columns: (auto, auto),
    align: (left, right),
    stroke: none,
    inset: 6pt,
    [Net], currency(net),
    [VAT #calc.round(data.vat_rate * 100, digits: 1)%], currency(vat),
    [*Total*], [*#currency(total)*],
  )
]
