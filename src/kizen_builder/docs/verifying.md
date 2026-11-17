# Verifying rendered output

A write that the CLI reports as successful can still be wrong on screen.
Nothing in the CLI renders a chart, an HTML body, or a resolved label — it
plans and applies the underlying data, and stops there. Confirming the
rendered result needs a human in the browser, every time the change touches
something a human looks at:

- **Dashboard and dashlet layout and data.** A dashlet spec that plans clean
  and applies clean can still duplicate on screen (a repeat `dashboards
  update` without the existing dashlet `id`s creates instead of updating) or
  render its data wrong (a filter the CLI reported as a clean success can
  still break the dashlet that consumes it). Open the dashboard.
- **Email and other HTML body rendering.** The CLI validates and stores the
  template; it has no renderer for the HTML that gets sent. Open the
  template or a test send.
- **Condition and filter label display.** A condition step's option values
  are stored and evaluated correctly even when the automation builder UI
  shows their labels as blank or malformed — this is a known display bug in
  the Kizen product's builder UI, not a CLI limitation. Confirm the
  underlying filter is correct (e.g. with `automations get` or a negative
  test against the gate) rather than trusting what the builder UI displays
  for the labels.

**The record Timeline is the single best artifact for confirming an
automation actually did what it was supposed to** — it shows the automation's
provenance against the record the way nothing else does, and the CLI has no
equivalent view today. Open it even when nothing looks wrong, not only when
chasing a failure.

How to confirm a run reached a terminal state:
`kizen docs show automation-runtime`.
