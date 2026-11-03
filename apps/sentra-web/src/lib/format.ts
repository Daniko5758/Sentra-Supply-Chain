/**
 * Display formatting required by PRD R-20.
 *
 * API values stay unchanged: these helpers only turn API values into text for
 * the UI. Currency values from the API are whole Rupiah (API_CONTRACT A.2).
 */

const BILLION = 1_000_000_000;
const MILLION = 1_000_000;
const DAY_MS = 24 * 60 * 60 * 1_000;
const MONTHS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
] as const;

const integerFormatter = new Intl.NumberFormat('en-US', {
  maximumFractionDigits: 0,
});

function oneDecimalFormatter() {
  return new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
    useGrouping: false,
  });
}

function twoDecimalFormatter() {
  return new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    useGrouping: false,
  });
}

function assertFiniteNumber(value: number, name: string): void {
  if (!Number.isFinite(value)) {
    throw new RangeError(`${name} must be a finite number.`);
  }
}

interface DateParts {
  year: number;
  month: number;
  day: number;
}

function parseIsoDate(value: string): DateParts {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) {
    throw new RangeError(`Expected an ISO date (YYYY-MM-DD), received ${value}.`);
  }

  const [, yearText, monthText, dayText] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const date = new Date(Date.UTC(year, month - 1, day));

  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  ) {
    throw new RangeError(`Expected a valid ISO date, received ${value}.`);
  }

  return { year, month, day };
}

function toUtcDay(parts: DateParts): number {
  return Date.UTC(parts.year, parts.month - 1, parts.day);
}

/** Formats an integer Rupiah amount as `IDR 3,286,000,000`. */
export function formatIDR(value: number): string {
  assertFiniteNumber(value, 'IDR value');
  return `IDR ${integerFormatter.format(value)}`;
}

/** Formats an IDR amount compactly, such as `IDR 3.29B` or `IDR 41.0M`. */
export function formatIDRShort(value: number): string {
  assertFiniteNumber(value, 'IDR value');

  const absoluteValue = Math.abs(value);
  if (absoluteValue >= MILLION) {
    // Pembulatan ke 1 desimal bisa menghasilkan 1000.0M; pada titik itu nilainya sudah miliar.
    const millions = oneDecimalFormatter().format(value / MILLION);
    if (absoluteValue < BILLION && Math.abs(Number(millions)) < 1_000) {
      return `IDR ${millions}M`;
    }
    return `IDR ${twoDecimalFormatter().format(value / BILLION)}B`;
  }

  return formatIDR(value);
}

/** Formats a score using R-20's one-decimal, half-up display rounding. */
export function formatScore(value: number): string {
  assertFiniteNumber(value, 'Score');
  return oneDecimalFormatter().format(value);
}

/** Formats a percent value using R-20's one-decimal, half-up display rounding. */
export function formatPercent(value: number): string {
  return `${formatScore(value)}%`;
}

/** Formats an API ISO date without interpreting it in the browser timezone. */
export function formatDate(value: string): string {
  const { year, month, day } = parseIsoDate(value);
  return `${String(day).padStart(2, '0')} ${MONTHS[month - 1]} ${year}`;
}

/** Like formatDate, but returns the API text unchanged when it is not an ISO date (never throws). */
export function formatDateOrRaw(value: string): string {
  try {
    return formatDate(value);
  } catch {
    return value;
  }
}

/** Formats a whole quantity with thousands separators, such as `1,250`. */
export function formatQuantity(value: number): string {
  assertFiniteNumber(value, 'Quantity');
  return integerFormatter.format(value);
}

/** Formats an ISO timestamp without shifting the date or time to the browser timezone. */
export function formatDateTime(value: string): string {
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?$/.exec(value);
  if (!match) {
    throw new RangeError(`Expected an ISO timestamp, received ${value}.`);
  }

  const [, date, hourText, minuteText] = match;
  parseIsoDate(date);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  if (hour > 23 || minute > 59) {
    throw new RangeError(`Expected a valid ISO timestamp, received ${value}.`);
  }

  return `${formatDate(date)}, ${hourText}:${minuteText}`;
}

/** Formats a date relative to the DEMO D0 date, for example `D+14`. */
export function formatDemoOffset(date: string, d0Date: string): string {
  const days = (toUtcDay(parseIsoDate(date)) - toUtcDay(parseIsoDate(d0Date))) / DAY_MS;

  if (days === 0) {
    return 'D0';
  }
  return days > 0 ? `D+${days}` : `D${days}`;
}

/** Formats a purchase-order line label required by API_CONTRACT A.2. */
export function formatPoLine(poId: string, itemNo: number): string {
  if (!Number.isInteger(itemNo) || itemNo < 0) {
    throw new RangeError('PO item number must be a non-negative integer.');
  }
  return `${poId}/${String(itemNo).padStart(2, '0')}`;
}
