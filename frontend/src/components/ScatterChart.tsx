import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

// ============================================================
// Types
// ============================================================

export type ChartPoint = {
  /** X value: number or ISO timestamp string */
  x: number | string;
  /** Y value */
  y: number;
  /** Optional label shown in tooltip */
  label?: string;
};

export type TimePreset = {
  label: string;
  /** Duration in milliseconds */
  ms: number;
};

const DEFAULT_TIME_PRESETS: TimePreset[] = [
  { label: '1小时', ms: 60 * 60 * 1000 },
  { label: '5小时', ms: 5 * 60 * 60 * 1000 },
  { label: '1天', ms: 24 * 60 * 60 * 1000 },
  { label: '1周', ms: 7 * 24 * 60 * 60 * 1000 },
];

export type ScatterChartProps = {
  /** Chart title shown in header */
  title?: string;
  /** Sub-label shown in header right (e.g. last value) */
  subLabel?: string;
  /** Data points */
  points: ChartPoint[];
  /** Y axis label */
  yLabel?: string;
  /** X axis label (only shown if not a time axis) */
  xLabel?: string;
  /** Whether the X axis represents time */
  timeAxis?: boolean;
  /** Custom time presets (only used when timeAxis=true) */
  timePresets?: TimePreset[];
  /**
   * Function to format Y axis tick values.
   * Defaults to `String(value)`.
   */
  formatY?: (value: number) => string;
  /**
   * Function to format the tooltip Y value.
   * Defaults to `formatY`.
   */
  formatTooltipY?: (value: number) => string;
  /** Class name applied to outer wrapper */
  className?: string;
  /** Show connecting line between points (default: true) */
  showLine?: boolean;
  /** Show individual dots (default: true) */
  showDots?: boolean;
  /** Approximate number of Y grid lines (default: 4) */
  yGridLines?: number;
  /** Chart height in CSS pixels (default: 160) */
  height?: number;
};

// ============================================================
// Fixed pixel constants — these are SCREEN pixels, not viewBox units.
// Because viewBox = actual pixel size, 1 viewBox unit = 1 px always.
// ============================================================

const CHART_H = 160;          // default chart height (px)
const PAD_LEFT = 50;          // Y-axis label area (px)
const PAD_RIGHT = 12;         // right margin (px)
const PAD_TOP = 10;           // top margin (px)
const PAD_BOTTOM = 30;        // X-axis label area (px)

// Visual constants (px — rendered 1:1 on screen regardless of container width)
const FONT_SIZE = 11;         // axis label font size (px)
const LINE_WIDTH = 1.5;       // chart line stroke-width (px)
const DOT_R = 2;              // dot radius (px)
const DOT_R_HOVER = 3.5;      // hovered dot radius (px)
const AXIS_STROKE = 1;        // axis baseline stroke-width (px)
const GRID_STROKE = 1;        // grid line stroke-width (px)
const CROSSHAIR_STROKE = 1;   // crosshair stroke-width (px)

// ============================================================
// Helpers
// ============================================================

function niceNumber(range: number, round: boolean): number {
  if (range === 0) return 1;
  const exponent = Math.floor(Math.log10(range));
  const fraction = range / Math.pow(10, exponent);
  let niceFraction: number;
  if (round) {
    if (fraction < 1.5) niceFraction = 1;
    else if (fraction < 3) niceFraction = 2;
    else if (fraction < 7) niceFraction = 5;
    else niceFraction = 10;
  } else {
    if (fraction <= 1) niceFraction = 1;
    else if (fraction <= 2) niceFraction = 2;
    else if (fraction <= 5) niceFraction = 5;
    else niceFraction = 10;
  }
  return niceFraction * Math.pow(10, exponent);
}

function niceScale(min: number, max: number, targetTicks: number): { min: number; max: number; step: number } {
  if (min === max) {
    const pad = Math.abs(min) * 0.1 || 1;
    min = min - pad;
    max = max + pad;
  }
  const range = niceNumber(max - min, false);
  const step = niceNumber(range / (targetTicks - 1), true);
  const niceMin = Math.floor(min / step) * step;
  const niceMax = Math.ceil(max / step) * step;
  return { min: niceMin, max: niceMax, step };
}

function formatTimestamp(ms: number, rangeMs: number): string {
  const d = new Date(ms);
  if (rangeMs < 2 * 60 * 60 * 1000) {
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  } else if (rangeMs < 3 * 24 * 60 * 60 * 1000) {
    return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  } else {
    return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }
}

function formatFullTimestamp(ms: number): string {
  const d = new Date(ms);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
}

function toNumericX(x: number | string): number {
  if (typeof x === 'number') return x;
  return new Date(x).getTime();
}

// ============================================================
// Main Component
// ============================================================

export function ScatterChart({
  title,
  subLabel,
  points,
  yLabel,
  xLabel,
  timeAxis = false,
  timePresets = DEFAULT_TIME_PRESETS,
  formatY = (v) => String(v),
  formatTooltipY,
  className,
  showLine = true,
  showDots = true,
  yGridLines = 4,
  height = CHART_H,
}: ScatterChartProps) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  // Actual pixel width of the SVG container, tracked via ResizeObserver.
  // This makes viewBox = "0 0 {svgW} {height}" so 1 viewBox unit = 1 CSS px.
  const [svgW, setSvgW] = useState(480);

  useEffect(() => {
    const el = wrapperRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w && w > 0) setSvgW(Math.round(w));
    });
    ro.observe(el);
    // seed initial value
    const w = el.getBoundingClientRect().width;
    if (w > 0) setSvgW(Math.round(w));
    return () => ro.disconnect();
  }, []);

  const svgH = height;
  const plotW = svgW - PAD_LEFT - PAD_RIGHT;
  const plotH = svgH - PAD_TOP - PAD_BOTTOM;

  const [tooltip, setTooltip] = useState<{ x: number; y: number; point: ChartPoint } | null>(null);
  const [timeRange, setTimeRange] = useState<number | null>(null);

  const fmtTooltipY = formatTooltipY ?? formatY;

  // ── Compute display data ──────────────────────────────────
  const allX = useMemo(() => points.map((p) => toNumericX(p.x)), [points]);
  const maxXTime = useMemo(() => (allX.length ? Math.max(...allX) : Date.now()), [allX]);

  const filteredPoints = useMemo(() => {
    if (!timeAxis || timeRange === null) return points;
    const cutoff = maxXTime - timeRange;
    return points.filter((p) => toNumericX(p.x) >= cutoff);
  }, [points, timeAxis, timeRange, maxXTime]);

  const filteredX = useMemo(() => filteredPoints.map((p) => toNumericX(p.x)), [filteredPoints]);
  const filteredY = useMemo(() => filteredPoints.map((p) => p.y), [filteredPoints]);

  const xMin = useMemo(() => (filteredX.length ? Math.min(...filteredX) : 0), [filteredX]);
  const xMax = useMemo(() => (filteredX.length ? Math.max(...filteredX) : 1), [filteredX]);
  const yMin = useMemo(() => (filteredY.length ? Math.min(...filteredY) : 0), [filteredY]);
  const yMax = useMemo(() => (filteredY.length ? Math.max(...filteredY) : 1), [filteredY]);

  const yScale = useMemo(() => niceScale(yMin, yMax, yGridLines), [yMin, yMax, yGridLines]);
  const xRangeMs = xMax - xMin;

  // ── Coordinate transforms (pixels) ───────────────────────
  const toSvgX = useCallback(
    (xVal: number) => {
      if (xMin === xMax) return PAD_LEFT + plotW / 2;
      return PAD_LEFT + ((xVal - xMin) / (xMax - xMin)) * plotW;
    },
    [xMin, xMax, plotW],
  );

  const toSvgY = useCallback(
    (yVal: number) => {
      const { min, max } = yScale;
      if (min === max) return PAD_TOP + plotH / 2;
      return PAD_TOP + (1 - (yVal - min) / (max - min)) * plotH;
    },
    [yScale, plotH],
  );

  // ── Ticks ─────────────────────────────────────────────────
  const yTicks = useMemo(() => {
    const ticks: number[] = [];
    const { min, max, step } = yScale;
    for (let v = min; v <= max + step * 0.001; v += step) {
      ticks.push(parseFloat(v.toPrecision(10)));
    }
    return ticks;
  }, [yScale]);

  const xTicks = useMemo(() => {
    if (filteredPoints.length === 0) return [];
    // Aim for roughly one tick every 80px
    const targetCount = Math.max(2, Math.min(6, Math.floor(plotW / 80)));
    if (timeAxis) {
      const step = niceNumber(xRangeMs / (targetCount - 1), true);
      const niceStart = Math.ceil(xMin / step) * step;
      const ticks: number[] = [];
      for (let t = niceStart; t <= xMax; t += step) {
        ticks.push(t);
      }
      if (ticks.length < 2) return [xMin, xMax];
      return ticks;
    } else {
      if (filteredPoints.length <= targetCount) return filteredX;
      const step = Math.round((filteredPoints.length - 1) / (targetCount - 1));
      return filteredX.filter((_, i) => i % step === 0);
    }
  }, [filteredPoints, filteredX, timeAxis, xMin, xMax, xRangeMs, plotW]);

  // ── Path string ───────────────────────────────────────────
  const linePath = useMemo(() => {
    if (filteredPoints.length < 2) return '';
    return filteredPoints
      .map((p, i) => {
        const sx = toSvgX(toNumericX(p.x));
        const sy = toSvgY(p.y);
        return `${i === 0 ? 'M' : 'L'} ${sx.toFixed(1)} ${sy.toFixed(1)}`;
      })
      .join(' ');
  }, [filteredPoints, toSvgX, toSvgY]);

  // ── Tooltip interaction ───────────────────────────────────
  const handleMouseMove = useCallback(
    (event: React.MouseEvent<SVGSVGElement>) => {
      if (filteredPoints.length === 0) return;
      const rect = svgRef.current?.getBoundingClientRect();
      if (!rect) return;

      // viewBox units = CSS pixels, so scaleX ≈ 1 (but respect actual render size)
      const scaleX = svgW / rect.width;
      const svgMouseX = (event.clientX - rect.left) * scaleX;
      const plotMouseX = svgMouseX - PAD_LEFT;

      if (plotMouseX < -8 || plotMouseX > plotW + 8) {
        setTooltip(null);
        return;
      }

      let nearestIdx = 0;
      let nearestDist = Infinity;
      filteredPoints.forEach((p, i) => {
        const sx = toSvgX(toNumericX(p.x)) - PAD_LEFT;
        const dist = Math.abs(sx - plotMouseX);
        if (dist < nearestDist) {
          nearestDist = dist;
          nearestIdx = i;
        }
      });

      const nearestPoint = filteredPoints[nearestIdx];
      const sx = toSvgX(toNumericX(nearestPoint.x));
      const sy = toSvgY(nearestPoint.y);
      setTooltip({ x: sx, y: sy, point: nearestPoint });
    },
    [filteredPoints, toSvgX, toSvgY, svgW, plotW],
  );

  const handleMouseLeave = useCallback(() => setTooltip(null), []);

  // ── Tooltip positioning (% of wrapper div) ────────────────
  const tooltipSide = tooltip && tooltip.x > svgW / 2 ? 'left' : 'right';
  const tooltipXPct = tooltip ? (tooltip.x / svgW) * 100 : 0;
  const tooltipYPct = tooltip ? (tooltip.y / svgH) * 100 : 0;

  const isEmpty = filteredPoints.length === 0;

  return (
    <div className={`scatter-chart${className ? ` ${className}` : ''}`}>
      {/* Header */}
      {(title !== undefined || subLabel !== undefined) && (
        <div className="scatter-chart-header">
          {title && <span className="scatter-chart-title">{title}</span>}
          {subLabel && <small className="scatter-chart-sublabel">{subLabel}</small>}
        </div>
      )}

      {/* Time range presets */}
      {timeAxis && timePresets.length > 0 && (
        <div className="scatter-chart-presets">
          <button
            type="button"
            className={`scatter-preset-btn${timeRange === null ? ' active' : ''}`}
            onClick={() => setTimeRange(null)}
          >
            全部
          </button>
          {timePresets.map((preset) => (
            <button
              key={preset.ms}
              type="button"
              className={`scatter-preset-btn${timeRange === preset.ms ? ' active' : ''}`}
              onClick={() => setTimeRange(preset.ms)}
            >
              {preset.label}
            </button>
          ))}
        </div>
      )}

      {/* Chart area */}
      <div className="scatter-chart-body">
        {yLabel && (
          <span className="scatter-y-label" aria-hidden="true">
            {yLabel}
          </span>
        )}

        {/* SVG wrapper — measured by ResizeObserver */}
        <div className="scatter-svg-wrapper" ref={wrapperRef} style={{ height: svgH }}>
          {isEmpty ? (
            <div className="scatter-empty">暂无数据</div>
          ) : (
            <svg
              ref={svgRef}
              /* viewBox matches actual pixel size → 1 unit = 1 px */
              viewBox={`0 0 ${svgW} ${svgH}`}
              width={svgW}
              height={svgH}
              className="scatter-svg"
              role="img"
              aria-label={title ?? '图表'}
              onMouseMove={handleMouseMove}
              onMouseLeave={handleMouseLeave}
            >
              {/* Y grid lines + labels */}
              {yTicks.map((tick) => {
                const sy = toSvgY(tick);
                return (
                  <g key={tick}>
                    <line
                      x1={PAD_LEFT}
                      y1={sy}
                      x2={PAD_LEFT + plotW}
                      y2={sy}
                      stroke="#e2e8f0"
                      strokeWidth={GRID_STROKE}
                      strokeDasharray="4 3"
                    />
                    <text
                      x={PAD_LEFT - 6}
                      y={sy}
                      fontSize={FONT_SIZE}
                      fill="#94a3b8"
                      fontFamily="inherit"
                      textAnchor="end"
                      dominantBaseline="middle"
                    >
                      {formatY(tick)}
                    </text>
                  </g>
                );
              })}

              {/* X axis baseline */}
              <line
                x1={PAD_LEFT}
                y1={PAD_TOP + plotH}
                x2={PAD_LEFT + plotW}
                y2={PAD_TOP + plotH}
                stroke="#cbd5e1"
                strokeWidth={AXIS_STROKE}
              />

              {/* Y axis baseline */}
              <line
                x1={PAD_LEFT}
                y1={PAD_TOP}
                x2={PAD_LEFT}
                y2={PAD_TOP + plotH}
                stroke="#cbd5e1"
                strokeWidth={AXIS_STROKE}
              />

              {/* X ticks + labels */}
              {xTicks.map((tick, i) => {
                const sx = toSvgX(tick);
                const label = timeAxis ? formatTimestamp(tick, xRangeMs) : formatY(tick);
                return (
                  <g key={`xtick-${i}`}>
                    <line
                      x1={sx}
                      y1={PAD_TOP + plotH}
                      x2={sx}
                      y2={PAD_TOP + plotH + 4}
                      stroke="#cbd5e1"
                      strokeWidth={AXIS_STROKE}
                    />
                    <text
                      x={sx}
                      y={PAD_TOP + plotH + FONT_SIZE + 4}
                      fontSize={FONT_SIZE}
                      fill="#94a3b8"
                      fontFamily="inherit"
                      textAnchor="middle"
                    >
                      {label}
                    </text>
                  </g>
                );
              })}

              {/* Line */}
              {showLine && linePath && (
                <path
                  d={linePath}
                  fill="none"
                  stroke="var(--accent, #1a73e8)"
                  strokeWidth={LINE_WIDTH}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              )}

              {/* Dots */}
              {showDots && filteredPoints.map((p, i) => {
                const sx = toSvgX(toNumericX(p.x));
                const sy = toSvgY(p.y);
                const isHovered = tooltip !== null && tooltip.point === p;
                return (
                  <circle
                    key={i}
                    cx={sx}
                    cy={sy}
                    r={isHovered ? DOT_R_HOVER : DOT_R}
                    fill="var(--accent, #1a73e8)"
                    opacity={isHovered ? 1 : 0.85}
                  />
                );
              })}

              {/* Crosshair */}
              {tooltip && (
                <line
                  x1={tooltip.x}
                  y1={PAD_TOP}
                  x2={tooltip.x}
                  y2={PAD_TOP + plotH}
                  stroke="var(--accent, #1a73e8)"
                  strokeWidth={CROSSHAIR_STROKE}
                  strokeDasharray="3 2"
                  opacity={0.5}
                />
              )}
            </svg>
          )}

          {/* Floating tooltip */}
          {tooltip && (
            <div
              className={`scatter-tooltip ${tooltipSide}`}
              style={{
                left: tooltipSide === 'right' ? `${tooltipXPct + 2}%` : undefined,
                right: tooltipSide === 'left' ? `${100 - tooltipXPct + 2}%` : undefined,
                top: `${Math.min(tooltipYPct, 70)}%`,
              }}
            >
              <span className="tooltip-y">{fmtTooltipY(tooltip.point.y)}</span>
              {timeAxis ? (
                <span className="tooltip-x">{formatFullTimestamp(toNumericX(tooltip.point.x))}</span>
              ) : (
                <span className="tooltip-x">
                  {tooltip.point.label ?? (xLabel ? `${xLabel}: ${toNumericX(tooltip.point.x)}` : String(toNumericX(tooltip.point.x)))}
                </span>
              )}
            </div>
          )}
        </div>

        {!timeAxis && xLabel && (
          <span className="scatter-x-label">{xLabel}</span>
        )}
      </div>
    </div>
  );
}
