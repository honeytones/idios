// Tracks jobs the user has created or interacted with via this dapp install.
// Backed by localStorage. Lost if user clears browser data.
// Future: could be replaced/augmented by parsing tx history once contract embeds job_id in comments.

const KEY = 'idios_tracked_jobs';

export interface TrackedJob {
  jobId: number;
  role: 'requester' | 'worker';
  addedAt: number;  // unix timestamp
  // Optional context captured at add-time (helpful for display)
  payment?: string;
  resultHash?: string;
}

export function getTrackedJobs(): TrackedJob[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function addTrackedJob(job: TrackedJob): void {
  const existing = getTrackedJobs();
  // Dedupe by jobId — if already present, update it
  const filtered = existing.filter(j => j.jobId !== job.jobId);
  filtered.unshift(job);
  try {
    localStorage.setItem(KEY, JSON.stringify(filtered));
  } catch (err) {
    console.error('Failed to save tracked job:', err);
  }
}

export function removeTrackedJob(jobId: number): void {
  const existing = getTrackedJobs();
  const filtered = existing.filter(j => j.jobId !== jobId);
  try {
    localStorage.setItem(KEY, JSON.stringify(filtered));
  } catch (err) {
    console.error('Failed to remove tracked job:', err);
  }
}

const ARBITRATOR_KEY = 'idios_arbitrator_jobs';

export interface TrackedArbitratorJob {
  jobId: number;
  addedAt: number;
}

export function getTrackedArbitratorJobs(): TrackedArbitratorJob[] {
  try {
    const raw = localStorage.getItem(ARBITRATOR_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function addTrackedArbitratorJob(job: TrackedArbitratorJob): void {
  const existing = getTrackedArbitratorJobs();
  const filtered = existing.filter(j => j.jobId !== job.jobId);
  filtered.unshift(job);
  try {
    localStorage.setItem(ARBITRATOR_KEY, JSON.stringify(filtered));
  } catch (err) {
    console.error('Failed to save tracked arbitrator job:', err);
  }
}

export function removeTrackedArbitratorJob(jobId: number): void {
  const existing = getTrackedArbitratorJobs();
  const filtered = existing.filter(j => j.jobId !== jobId);
  try {
    localStorage.setItem(ARBITRATOR_KEY, JSON.stringify(filtered));
  } catch (err) {
    console.error('Failed to remove tracked arbitrator job:', err);
  }
}
