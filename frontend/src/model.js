export const OPEN_EXECUTION = new Set(['READY', 'ACTIVE', 'WAITING']);

export function label(value = '') {
  return String(value).replaceAll('_', ' ').toLowerCase().replace(/\b\w/g, c => c.toUpperCase());
}

export function statusTone(value = '') {
  if (['COMPLETED', 'CLOSED'].includes(value)) return 'success';
  if (['FAILED', 'CANCELLED'].includes(value)) return 'danger';
  if (['IN_REVIEW', 'IN_PROGRESS', 'ACTIVE'].includes(value)) return 'accent';
  if (['WAITING', 'SUBMITTED', 'READY_TO_CLOSE'].includes(value)) return 'warning';
  return 'neutral';
}

export function workActions(item) {
  if (['READY', 'ASSIGNED'].includes(item.state)) return ['start'];
  if (['IN_PROGRESS', 'RESPONSE_RECEIVED'].includes(item.state)) return ['complete'];
  return [];
}

export function shortDate(value) {
  if (!value) return '—';
  return new Intl.DateTimeFormat('en', {month: 'short', day: 'numeric', year: 'numeric'})
    .format(new Date(value));
}
