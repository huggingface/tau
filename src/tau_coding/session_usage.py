"""Token-usage analytics for Tau sessions, rendered as an HTML dashboard."""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass

from tau_agent.messages import AssistantMessage
from tau_agent.session import CompactionEntry, MessageEntry, SessionEntry
from tau_coding.provider_catalog import builtin_provider_entry, model_cost_for_input_tokens

__all__ = [
    "RequestUsage",
    "SessionUsage",
    "USAGE_SCRIPT",
    "USAGE_STYLES",
    "collect_session_usage",
    "render_usage_dashboard",
]


@dataclass(frozen=True, slots=True)
class RequestUsage:
    """Token usage for a single assistant response."""

    number: int
    provider: str
    model: str
    fresh: int
    cached: int
    cache_write: int
    cache_write_1h: int
    output: int
    reasoning: int
    stop_reason: str
    estimated_cost: float | None

    @property
    def prompt(self) -> int:
        return self.fresh + self.cached + self.cache_write

    @property
    def hit_rate(self) -> float:
        return self.cached / self.prompt if self.prompt else 0.0


@dataclass(frozen=True, slots=True)
class SessionUsage:
    """Aggregated usage for the entries shown in an export."""

    requests: tuple[RequestUsage, ...]
    tool_calls: tuple[tuple[str, int], ...]
    compactions: int

    @property
    def total_fresh(self) -> int:
        return sum(item.fresh for item in self.requests)

    @property
    def total_cached(self) -> int:
        return sum(item.cached for item in self.requests)

    @property
    def total_cache_write(self) -> int:
        return sum(item.cache_write for item in self.requests)

    @property
    def total_prompt(self) -> int:
        return self.total_fresh + self.total_cached + self.total_cache_write

    @property
    def total_output(self) -> int:
        return sum(item.output for item in self.requests)

    @property
    def hit_rate(self) -> float:
        return self.total_cached / self.total_prompt if self.total_prompt else 0.0

    @property
    def total_cost(self) -> float | None:
        costs = [item.estimated_cost for item in self.requests]
        if not costs or any(cost is None for cost in costs):
            return None
        return sum(cost for cost in costs if cost is not None)


def estimated_request_cost(
    provider: str,
    model: str,
    *,
    fresh: int,
    cached: int,
    cache_write: int,
    cache_write_1h: int,
    output: int,
) -> float | None:
    """Estimate a request cost in USD from the built-in provider catalog rates."""
    entry = builtin_provider_entry(provider)
    metadata = entry.model_metadata.get(model) if entry is not None else None
    if metadata is None:
        return None
    rates = model_cost_for_input_tokens(metadata, fresh + cached + cache_write)
    if not rates:
        return None
    # cache_write already includes the 1-hour TTL writes; Anthropic bills those at
    # the higher cacheWrite1h rate, falling back to the 5-minute rate when the
    # catalog entry has no cacheWrite1h value.
    write_1h = min(cache_write_1h, cache_write)
    return (
        fresh * rates.get("input", 0.0)
        + cached * rates.get("cacheRead", 0.0)
        + (cache_write - write_1h) * rates.get("cacheWrite", 0.0)
        + write_1h * rates.get("cacheWrite1h", rates.get("cacheWrite", 0.0))
        + output * rates.get("output", 0.0)
    ) / 1_000_000


def collect_session_usage(entries: Sequence[SessionEntry]) -> SessionUsage:
    """Collect per-request token usage, tool-call counts, and compactions."""
    requests: list[RequestUsage] = []
    tools: dict[str, int] = {}
    compactions = 0
    for entry in entries:
        if isinstance(entry, CompactionEntry):
            compactions += 1
            continue
        if not isinstance(entry, MessageEntry):
            continue
        message = entry.message
        if not isinstance(message, AssistantMessage):
            continue
        for call in message.tool_calls:
            tools[call.name] = tools.get(call.name, 0) + 1
        usage = message.usage
        cache_write_1h = usage.cache_write_1h or 0
        estimated = estimated_request_cost(
            message.provider,
            message.model,
            fresh=usage.input,
            cached=usage.cache_read,
            cache_write=usage.cache_write,
            cache_write_1h=cache_write_1h,
            output=usage.output,
        )
        requests.append(
            RequestUsage(
                number=len(requests) + 1,
                provider=message.provider,
                model=message.model,
                fresh=usage.input,
                cached=usage.cache_read,
                cache_write=usage.cache_write,
                cache_write_1h=cache_write_1h,
                output=usage.output,
                reasoning=usage.reasoning or 0,
                stop_reason=message.stop_reason,
                estimated_cost=estimated,
            )
        )
    ordered_tools = tuple(sorted(tools.items(), key=lambda item: (-item[1], item[0])))
    return SessionUsage(requests=tuple(requests), tool_calls=ordered_tools, compactions=compactions)


_SERIES_COLORS = {
    "cached": "#1f9d94",
    "cache writes": "#3fa34d",
    "fresh": "#e5484d",
    "request": "#e5484d",
    "cumulative": "#4c6ef5",
    "output": "#9c56d6",
    "reasoning": "#3fa34d",
}


def _compact_number(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}m"
    if value >= 1_000:
        return f"{value / 1_000:.0f}k"
    return f"{value:.0f}"


def _format_cost(value: float | None) -> str:
    if value is None:
        return "$N/A"
    if 0 < value < 0.01:
        return f"${value:.3f}"
    return f"${value:.2f}"


def _line_chart(
    title: str,
    series: Sequence[tuple[str, Sequence[float]]],
    *,
    y_max: float | None = None,
    percent: bool = False,
) -> str:
    """Render one interactive line chart as inline SVG."""
    width, height = 900, 330
    left, right, top, bottom = 68, 20, 42, 52
    plot_width, plot_height = width - left - right, height - top - bottom
    count = max((len(values) for _, values in series), default=0)
    observed_max = max((max(values, default=0.0) for _, values in series), default=0.0)
    maximum = y_max or max(observed_max, 1.0)
    if not percent:
        magnitude = 10 ** max(0, len(str(int(maximum))) - 1)
        maximum = ((maximum + magnitude - 1) // magnitude) * magnitude

    def point(index: int, value: float) -> tuple[float, float]:
        x = left + (index / max(count - 1, 1)) * plot_width
        y = top + plot_height - (value / maximum) * plot_height
        return x, y

    parts = [
        f'<svg class="usage-chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{html.escape(title, quote=True)}" data-left="{left}" '
        f'data-right="{right}" data-count="{count}">',
        f'<text class="chart-title" x="{left}" y="24">{html.escape(title)}</text>',
    ]
    for tick in range(6):
        value = maximum * tick / 5
        y = top + plot_height - tick * plot_height / 5
        label = f"{value:.0%}" if percent else _compact_number(value)
        parts.append(
            f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}"/>'
            f'<text class="tick" x="{left - 9}" y="{y + 4:.1f}" text-anchor="end">{label}</text>'
        )
    for index in range(count):
        if count <= 20 or index % max(1, count // 12) == 0 or index == count - 1:
            x, _ = point(index, 0)
            parts.append(
                f'<text class="tick" x="{x:.1f}" y="{height - 22}" '
                f'text-anchor="middle">{index + 1}</text>'
            )
    parts.append(
        f'<line class="hover-line" x1="{left}" y1="{top}" x2="{left}" '
        f'y2="{top + plot_height}" visibility="hidden"/>'
    )
    for series_index, (name, values) in enumerate(series):
        color = _SERIES_COLORS.get(name, "#4c6ef5")
        points = " ".join(
            f"{x:.1f},{y:.1f}" for x, y in (point(i, value) for i, value in enumerate(values))
        )
        parts.append(f'<g class="series" data-series-id="{series_index}">')
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.5"/>'
        )
        for index, value in enumerate(values):
            x, y = point(index, value)
            label = f"{value:.1%}" if percent else f"{int(value):,}"
            parts.append(
                f'<circle class="point" data-index="{index}" '
                f'data-name="{html.escape(name, quote=True)}" data-label="{label}" '
                f'cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="{color}"/>'
            )
        parts.append("</g>")
    legend_x = float(width - right)
    for series_index, (name, _) in reversed(list(enumerate(series))):
        legend_x -= 18 + len(name) * 8
        parts.append(
            f'<g class="series-toggle" data-series-id="{series_index}" role="button" '
            f'tabindex="0" aria-pressed="true">'
            f'<circle cx="{legend_x:.1f}" cy="20" r="4" '
            f'fill="{_SERIES_COLORS.get(name, "#4c6ef5")}"/>'
            f'<text class="legend" x="{legend_x + 8:.1f}" y="24">{html.escape(name)}</text></g>'
        )
    parts.append(
        f'<text class="axis-label" x="{left + plot_width / 2}" y="{height - 4}" '
        'text-anchor="middle">Model request</text></svg>'
    )
    return "".join(parts)


def _figure(chart: str) -> str:
    return (
        f'<figure class="usage-figure">{chart}'
        '<button type="button" class="png-button" '
        'title="Download this chart as a PNG image">PNG</button></figure>'
    )


def render_usage_dashboard(usage: SessionUsage) -> str:
    """Render the usage dashboard markup for the export's Usage tab."""
    requests = usage.requests
    if not requests:
        return (
            '<p class="empty">No assistant responses with token usage were found '
            "in this session.</p>"
        )

    cached = [float(item.cached) for item in requests]
    cache_writes = [float(item.cache_write) for item in requests]
    fresh = [float(item.fresh) for item in requests]
    outputs = [float(item.output) for item in requests]
    reasoning = [float(item.reasoning) for item in requests]
    rates = [item.hit_rate for item in requests]
    cumulative_rates: list[float] = []
    running_cached = running_prompt = 0
    for item in requests:
        running_cached += item.cached
        running_prompt += item.prompt
        cumulative_rates.append(running_cached / running_prompt if running_prompt else 0.0)

    cards = [
        ("Model requests", f"{len(requests):,}"),
        ("Cache hit rate", f"{usage.hit_rate:.1%}"),
        ("Cached input", f"{usage.total_cached:,}"),
        ("Cache writes", f"{usage.total_cache_write:,}"),
        ("Fresh input", f"{usage.total_fresh:,}"),
        ("Total prompt input", f"{usage.total_prompt:,}"),
        ("Output tokens", f"{usage.total_output:,}"),
        ("Estimated cost", _format_cost(usage.total_cost)),
        ("Compactions", f"{usage.compactions}"),
    ]
    cards_html = "".join(
        f'<div class="usage-card"><span>{html.escape(label)}</span>'
        f"<strong>{html.escape(value)}</strong></div>"
        for label, value in cards
    )

    charts_html = "".join(
        _figure(chart)
        for chart in (
            _line_chart(
                "Prompt input by request",
                [("cached", cached), ("cache writes", cache_writes), ("fresh", fresh)],
            ),
            _line_chart(
                "Cache hit rate",
                [("request", rates), ("cumulative", cumulative_rates)],
                y_max=1.0,
                percent=True,
            ),
            _line_chart(
                "Output and reasoning tokens",
                [("output", outputs), ("reasoning", reasoning)],
            ),
        )
    )

    table_rows = "".join(
        f"<tr><td>{item.number}</td><td>{html.escape(item.provider)}</td>"
        f"<td>{html.escape(item.model)}</td><td>{item.fresh:,}</td><td>{item.cached:,}</td>"
        f"<td>{item.cache_write:,}</td><td>{item.prompt:,}</td><td>{item.hit_rate:.1%}</td>"
        f"<td>{item.output:,}</td><td>{_format_cost(item.estimated_cost)}</td>"
        f"<td>{html.escape(item.stop_reason)}</td></tr>"
        for item in requests
    )
    tool_rows = (
        "".join(
            f'<div class="usage-tool"><span>{html.escape(name)}</span>'
            f"<strong>{count}</strong></div>"
            for name, count in usage.tool_calls
        )
        or '<p class="empty">No tool calls.</p>'
    )

    return (
        f'<div class="usage-cards">{cards_html}</div>'
        '<p class="usage-note">Costs use Tau\'s provider catalog rates. OAuth subscription '
        "estimates are API-rate equivalents, not actual subscription charges. Hover a request "
        "for exact values, select a legend item to hide a series, and use PNG to save a "
        "chart.</p>"
        f'<div class="usage-charts">{charts_html}</div>'
        '<div class="usage-details">'
        '<div class="usage-panel"><h2>Requests</h2><div class="usage-table-wrap"><table>'
        "<thead><tr><th>#</th><th>Provider</th><th>Model</th><th>Fresh</th><th>Cached</th>"
        "<th>Written</th><th>Prompt</th><th>Hit rate</th><th>Output</th><th>Est. cost</th>"
        f"<th>Stop</th></tr></thead><tbody>{table_rows}</tbody></table></div></div>"
        f'<div class="usage-panel"><h2>Tool calls</h2>{tool_rows}</div>'
        "</div>"
        '<div class="usage-tooltip" role="status" aria-live="polite"></div>'
    )


USAGE_STYLES = """
    .usage-cards {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }
    .usage-card {
      min-width: 0;
      padding: 12px 14px;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 10px;
    }
    .usage-card span {
      display: block;
      color: var(--muted);
      font-family: var(--sans);
      font-size: 0.64rem;
      font-weight: 500;
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }
    .usage-card strong {
      display: block;
      margin-top: 5px;
      font-family: var(--sans);
      font-size: 1.15rem;
      font-weight: 500;
      font-variant-numeric: tabular-nums;
    }
    .usage-note {
      margin: 14px 0 0;
      padding-left: 10px;
      border-left: 2px solid var(--line-strong);
      color: var(--muted);
      font-family: var(--sans);
      font-size: 0.76rem;
    }
    .usage-charts {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      margin-top: 14px;
    }
    .usage-figure {
      position: relative;
      margin: 0;
      padding: 12px 12px 4px;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 10px;
    }
    .usage-figure:first-child { grid-column: 1 / -1; }
    .usage-chart {
      display: block;
      width: 100%;
      height: auto;
      overflow: visible;
      cursor: crosshair;
      touch-action: none;
    }
    .png-button {
      position: absolute;
      top: 10px;
      right: 10px;
      padding: 3px 9px;
      color: var(--muted);
      background: var(--surface);
      border: 1px solid var(--line-strong);
      border-radius: 999px;
      font-family: var(--sans);
      font-size: 0.66rem;
      font-weight: 500;
      letter-spacing: 0.08em;
      cursor: pointer;
      transition: color .15s, border-color .15s;
    }
    .png-button:hover { color: var(--accent); border-color: var(--accent); }
    .png-button[disabled] { opacity: .5; cursor: progress; }
    .grid { stroke: var(--line); stroke-width: 1; }
    .chart-title {
      fill: var(--text);
      font-family: var(--sans);
      font-size: 14px;
      font-weight: 500;
    }
    .tick, .axis-label, .legend {
      fill: var(--muted);
      font-family: var(--sans);
      font-size: 11px;
    }
    .hover-line {
      stroke: var(--accent);
      stroke-width: 1;
      stroke-dasharray: 3 4;
      pointer-events: none;
    }
    .point { transition: r .12s ease; }
    .point.active { r: 5.5; stroke: var(--surface); stroke-width: 2; }
    .series { transition: opacity .15s ease; }
    .series.is-hidden { opacity: .08; pointer-events: none; }
    .series-toggle { cursor: pointer; outline: none; }
    .series-toggle[aria-pressed="false"] { opacity: .3; }
    .series-toggle:hover text { text-decoration: underline; }
    .usage-tooltip {
      position: fixed;
      z-index: 30;
      display: none;
      min-width: 170px;
      padding: 8px 11px;
      background: var(--surface);
      color: var(--text);
      border: 1px solid var(--line-strong);
      border-radius: 8px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, .18);
      font-family: var(--mono);
      font-size: 0.74rem;
      line-height: 1.6;
      white-space: pre-line;
      pointer-events: none;
    }
    .usage-details {
      display: grid;
      grid-template-columns: minmax(0, 3fr) minmax(180px, 1fr);
      gap: 12px;
      margin-top: 12px;
    }
    .usage-panel {
      min-width: 0;
      padding: 14px;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 10px;
    }
    .usage-table-wrap {
      overflow: auto;
      max-height: 520px;
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .usage-panel table {
      border-collapse: collapse;
      width: 100%;
      white-space: nowrap;
      font-family: var(--sans);
      font-size: 0.74rem;
      font-variant-numeric: tabular-nums;
    }
    .usage-panel th, .usage-panel td {
      padding: 6px 9px;
      text-align: right;
      border-bottom: 1px solid var(--line);
    }
    .usage-panel th {
      position: sticky;
      top: 0;
      z-index: 1;
      background: var(--surface-muted);
      color: var(--muted);
      font-size: 0.62rem;
      font-weight: 600;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    .usage-panel th:nth-child(2), .usage-panel th:nth-child(3),
    .usage-panel td:nth-child(2), .usage-panel td:nth-child(3) { text-align: left; }
    .usage-tool {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      padding: 6px 2px;
      border-bottom: 1px solid var(--line);
      font-family: var(--sans);
      font-size: 0.76rem;
    }
    .usage-tool:last-child { border-bottom: none; }
    .usage-tool strong { color: var(--accent); font-weight: 500; }
    @media (max-width: 820px) {
      .usage-cards { grid-template-columns: 1fr 1fr; }
      .usage-charts { grid-template-columns: 1fr; }
      .usage-figure:first-child { grid-column: auto; }
      .usage-details { grid-template-columns: 1fr; }
    }
"""


USAGE_SCRIPT = """
    (function () {
      var tooltip = document.querySelector(".usage-tooltip");
      if (!tooltip) {
        return;
      }
      function hideTooltip(chart) {
        tooltip.style.display = "none";
        var line = chart.querySelector(".hover-line");
        if (line) {
          line.setAttribute("visibility", "hidden");
        }
        chart.querySelectorAll(".point.active").forEach(function (point) {
          point.classList.remove("active");
        });
      }
      document.querySelectorAll(".usage-chart").forEach(function (chart) {
        var count = Number(chart.dataset.count);
        var left = Number(chart.dataset.left);
        var right = Number(chart.dataset.right);
        var viewWidth = chart.viewBox.baseVal.width;
        chart.addEventListener("pointermove", function (event) {
          var rect = chart.getBoundingClientRect();
          var svgX = (event.clientX - rect.left) * viewWidth / rect.width;
          var plotWidth = viewWidth - left - right;
          if (svgX < left || svgX > viewWidth - right || count < 1) {
            hideTooltip(chart);
            return;
          }
          var ratio = (svgX - left) / plotWidth * Math.max(count - 1, 1);
          var index = Math.max(0, Math.min(count - 1, Math.round(ratio)));
          var selector = '.point[data-index="' + index + '"]';
          var points = Array.prototype.filter.call(
            chart.querySelectorAll(selector),
            function (point) {
              return !point.closest(".series").classList.contains("is-hidden");
            }
          );
          if (!points.length) {
            return;
          }
          chart.querySelectorAll(".point.active").forEach(function (point) {
            point.classList.remove("active");
          });
          points.forEach(function (point) {
            point.classList.add("active");
          });
          var x = points[0].getAttribute("cx");
          var line = chart.querySelector(".hover-line");
          line.setAttribute("x1", x);
          line.setAttribute("x2", x);
          line.setAttribute("visibility", "visible");
          tooltip.textContent = "Request " + (index + 1) + "\\n" + points.map(function (point) {
            return point.dataset.name + "  " + point.dataset.label;
          }).join("\\n");
          tooltip.style.display = "block";
          var tooltipRect = tooltip.getBoundingClientRect();
          var maxLeft = window.innerWidth - tooltipRect.width - 10;
          tooltip.style.left = Math.min(maxLeft, event.clientX + 14) + "px";
          tooltip.style.top = Math.max(10, event.clientY - tooltipRect.height - 12) + "px";
        });
        chart.addEventListener("pointerleave", function () {
          hideTooltip(chart);
        });
        function toggleSeries(control) {
          var id = control.dataset.seriesId;
          var series = chart.querySelector('.series[data-series-id="' + id + '"]');
          var visible = control.getAttribute("aria-pressed") === "true";
          control.setAttribute("aria-pressed", String(!visible));
          series.classList.toggle("is-hidden", visible);
        }
        chart.querySelectorAll(".series-toggle").forEach(function (control) {
          control.addEventListener("click", function (event) {
            event.stopPropagation();
            toggleSeries(control);
          });
          control.addEventListener("keydown", function (event) {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              toggleSeries(control);
            }
          });
        });
      });

      var SVG_NS = "http:" + "//www.w3.org/2000/svg";
      var PRINT_CSS = [
        'text{font-family:"Space Grotesk",Helvetica,Arial,sans-serif}',
        ".chart-title{fill:#13213c;font-size:14px;font-weight:600}",
        ".tick,.axis-label,.legend{fill:#5a667e;font-size:11px}",
        ".grid{stroke:#dce4f2;stroke-width:1}",
        ".point{stroke:#ffffff;stroke-width:1}"
      ].join("");

      function staticSvg(chart) {
        var width = chart.viewBox.baseVal.width;
        var height = chart.viewBox.baseVal.height;
        var clone = chart.cloneNode(true);
        clone.setAttribute("xmlns", SVG_NS);
        clone.setAttribute("width", width);
        clone.setAttribute("height", height);
        clone.querySelectorAll(".hover-line").forEach(function (node) {
          node.remove();
        });
        clone.querySelectorAll(".series.is-hidden").forEach(function (node) {
          var id = node.dataset.seriesId;
          var control = clone.querySelector('.series-toggle[data-series-id="' + id + '"]');
          if (control) {
            control.remove();
          }
          node.remove();
        });
        clone.querySelectorAll(".point.active").forEach(function (node) {
          node.classList.remove("active");
        });
        var style = document.createElementNS(SVG_NS, "style");
        style.textContent = PRINT_CSS;
        var background = document.createElementNS(SVG_NS, "rect");
        background.setAttribute("x", "0");
        background.setAttribute("y", "0");
        background.setAttribute("width", width);
        background.setAttribute("height", height);
        background.setAttribute("fill", "#ffffff");
        clone.insertBefore(background, clone.firstChild);
        clone.insertBefore(style, clone.firstChild);
        return {
          markup: new XMLSerializer().serializeToString(clone),
          width: width,
          height: height
        };
      }

      function downloadPng(chart, button) {
        var data = staticSvg(chart);
        var scale = 2;
        var image = new Image();
        image.onload = function () {
          var canvas = document.createElement("canvas");
          canvas.width = data.width * scale;
          canvas.height = data.height * scale;
          var context = canvas.getContext("2d");
          context.fillStyle = "#ffffff";
          context.fillRect(0, 0, canvas.width, canvas.height);
          context.drawImage(image, 0, 0, canvas.width, canvas.height);
          canvas.toBlob(function (blob) {
            var url = URL.createObjectURL(blob);
            var link = document.createElement("a");
            var title = chart.getAttribute("aria-label") || "chart";
            link.href = url;
            link.download = title.toLowerCase().replace(/[^a-z0-9]+/g, "-")
              .replace(/^-|-$/g, "") + ".png";
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);
            button.disabled = false;
          }, "image/png");
        };
        image.onerror = function () {
          button.disabled = false;
          button.textContent = "failed";
        };
        image.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(data.markup);
      }

      document.querySelectorAll(".png-button").forEach(function (button) {
        button.addEventListener("click", function () {
          var chart = button.parentNode.querySelector(".usage-chart");
          if (!chart) {
            return;
          }
          button.disabled = true;
          downloadPng(chart, button);
        });
      });
    })();
"""
