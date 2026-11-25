import { useId } from 'react';

export interface PlotBucket {
  key: string;
  label: string;
  count: number;
}
const LEFT = 36;
const BASELINE = 164;

export function ActivityPlot({
  buckets,
  title,
}: {
  buckets: PlotBucket[];
  title: string;
}) {
  const titleId = useId();
  const max = Math.max(1, ...buckets.map((row) => row.count));
  const step = 672 / Math.max(1, buckets.length);
  const y = (count: number) => BASELINE - (count / max) * 136;
  const x = (index: number) => LEFT + (index + 0.5) * step;
  const points = buckets.map((row, i) => `${x(i)},${y(row.count)}`).join(' ');
  return (
    <div className="posting-plot-scroll">
      <svg
        className="posting-plot"
        viewBox="0 0 720 200"
        role="img"
        aria-labelledby={titleId}
      >
        <title id={titleId}>{title}</title>
        {[...new Set([0, Math.ceil(max / 2), max])].map((count) => (
          <g key={count} className="posting-grid-line">
            <line x1={LEFT} x2="708" y1={y(count)} y2={y(count)} />
            <text x="28" y={y(count) + 4} textAnchor="end">
              {count}
            </text>
          </g>
        ))}
        {buckets.map((row, i) => (
          <g key={row.key}>
            <rect
              data-posting-hour={row.key}
              data-count={row.count}
              x={x(i) - step * 0.3}
              y={y(row.count)}
              width={step * 0.6}
              height={BASELINE - y(row.count)}
              rx="2"
            >
              <title>
                {row.label}：{row.count} 条
              </title>
            </rect>
            {i % Math.ceil(buckets.length / 6) === 0 && (
              <text className="posting-hour-label" x={x(i)} y="188" textAnchor="middle">
                {row.label}
              </text>
            )}
          </g>
        ))}
        <polyline points={points} />
        {buckets.map((row, i) => (
          <circle key={row.key} cx={x(i)} cy={y(row.count)} r="2.5">
            <title>
              {row.label}：{row.count} 条
            </title>
          </circle>
        ))}
      </svg>
    </div>
  );
}
