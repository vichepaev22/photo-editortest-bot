export const RETURN_AFTER_MS = 7 * 24 * 60 * 60 * 1000;

export function welcomeDue({seen, lastVisit, now, force=false, closedInSession=false}) {
  if (force) return true;
  if (closedInSession) return false;
  if (!seen) return true;
  return Number.isFinite(lastVisit) && lastVisit > 0 && now - lastVisit >= RETURN_AFTER_MS;
}
