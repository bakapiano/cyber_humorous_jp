export interface PublicationSample {
  id: string;
  publishedAt: string | null;
}

export interface HourCount {
  hour: number;
  count: number;
}

export function publicationHours(
  samples: PublicationSample[],
  hours: number,
  now: number,
) {
  const buckets: HourCount[] = Array.from({ length: 24 }, (_, hour) => ({
    hour,
    count: 0,
  }));
  const seen = new Set<string>();
  const since = now - hours * 3_600_000;
  let total = 0;
  let missing = 0;
  for (const sample of samples) {
    if (seen.has(sample.id)) continue;
    seen.add(sample.id);
    const time = datedPublication(sample.publishedAt);
    if (time === null) {
      missing += 1;
      continue;
    }
    if (time < since || time > now) continue;
    const hour = new Date(time + 8 * 3_600_000).getUTCHours();
    const bucket = buckets[hour];
    if (bucket) bucket.count += 1;
    total += 1;
  }
  const maximum = Math.max(...buckets.map((bucket) => bucket.count));
  const peaks = buckets.filter((bucket) => maximum > 0 && bucket.count === maximum);
  return { buckets, total, missing, peaks, maximum, since, until: now };
}

function datedPublication(value: string | null): number | null {
  if (!value || !/(Z|[+-]\d{2}:\d{2})$/i.test(value)) return null;
  const time = Date.parse(value);
  return Number.isFinite(time) ? time : null;
}

export function hourLabel(hour: number): string {
  return `${String(hour).padStart(2, '0')}:00`;
}

export function publicationRange(time: number): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(time);
}
