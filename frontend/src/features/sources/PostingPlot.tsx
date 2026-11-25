import { useId } from 'react';
import { hourLabel } from './posting-model';
import type { HourCount } from './posting-model';

const LEFT = 36;
const STEP = 28;
const BASELINE = 164;
const HEIGHT = 136;

export function PostingPlot({ buckets }: { buckets: HourCount[] }) {
  const titleId = useId();
  const max = Math.max(1, ...buckets.map((row) => row.count));
  const ticks = [...new Set([0, Math.ceil(max / 2), max])];
  const y = (count: number) => BASELINE - (count / max) * HEIGHT;
  const points = buckets
    .map((row) => `${LEFT + (row.hour + 0.5) * STEP},${y(row.count)}`)
    .join(' ');
  return (
    <div className="posting-plot-scroll">
      <svg
        className="posting-plot"
        viewBox="0 0 720 200"
        role="img"
        aria-labelledby={titleId}
      >
        <title id={titleId}>按北京时间统计的每小时来源投稿量</title>
        {ticks.map((count) => (
          <g key={count} className="posting-grid-line">
            <line x1={LEFT} x2="708" y1={y(count)} y2={y(count)} />
            <text x="28" y={y(count) + 4} textAnchor="end">
              {count}
            </text>
          </g>
        ))}
        {buckets.map((row) => (
          <g key={row.hour}>
            <rect
              data-posting-hour={row.hour}
              data-count={row.count}
              x={LEFT + row.hour * STEP + 6}
              y={y(row.count)}
              width="16"
              height={BASELINE - y(row.count)}
              rx="3"
            >
              <title>
                {hourLabel(row.hour)}–{String(row.hour).padStart(2, '0')}:59：{row.count}{' '}
                条
              </title>
            </rect>
            {row.hour % 3 === 0 && (
              <text
                className="posting-hour-label"
                x={LEFT + (row.hour + 0.5) * STEP}
                y="188"
                textAnchor="middle"
              >
                {hourLabel(row.hour)}
              </text>
            )}
          </g>
        ))}
        <polyline points={points} />
        {buckets.map((row) => (
          <circle
            key={row.hour}
            cx={LEFT + (row.hour + 0.5) * STEP}
            cy={y(row.count)}
            r="2.5"
          >
            <title>
              {hourLabel(row.hour)}：{row.count} 条
            </title>
          </circle>
        ))}
      </svg>
    </div>
  );
}
