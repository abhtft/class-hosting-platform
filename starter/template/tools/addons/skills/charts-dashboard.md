# Charts and dashboards

Use this when the student asks for a chart, a statistics page or a dashboard. Good charts answer one question quickly. Fewer, clearer charts beat many busy ones.

## Choose the chart for the question
- **Change over time:** a line chart (many points) or a column chart (a few periods).
- **Compare categories:** a horizontal bar chart, sorted from largest to smallest. Long labels fit better on the side.
- **Share of a whole:** a stacked bar or, for two to four parts only, a pie or donut. If the parts are close in size, use bars instead.
- **Relationship between two numbers:** a scatter chart.
- **One number that matters:** show it large as a figure with a short label and, if useful, a change since last time. Do not draw a chart for one number.

## Make it readable
- Title every chart with what it shows, in words ("Quiz scores by week"). Add the unit (marks, minutes, percent) to the axis or the label.
- Start bar charts at zero. Line charts may start higher, but say so if it is not obvious.
- Few colours: one colour for one series, a second for emphasis. Do not rely on colour alone; label lines directly or add a legend that is easy to match.
- Light grid lines, no 3D, no shadows, no decoration. Show values on bars when there are few of them.
- Choose sensible number formats: thousands separators, one or two decimals at most, dates in a short form.

## Dashboard layout
- Put the most important figures in a row at the top (three to five small summary cards).
- Below them, put charts in a grid, two columns on wide screens and one column on phones.
- Keep filters (date range, class, subject) together at the top. Changing a filter updates every chart.
- Always build the loading, empty and error states for each chart. An empty chart should say "No data yet" and what to do.

## Data
- Charts read plain arrays of objects from the backend, for example `[{ "week": "W1", "score": 62 }]`. Keep the shape simple and the field names clear.
- Never put secrets or other students' personal data in the chart data. Show totals or averages when individual values are private.
- Check totals: the numbers shown in the summary cards must match what the charts show.

## If a chart library is added
- Wrap each chart in a container with a set height; most chart libraries need one.
- Make the chart resize with its container.
- Give the chart a text summary nearby (one sentence with the key result) so screen reader users get the point.
