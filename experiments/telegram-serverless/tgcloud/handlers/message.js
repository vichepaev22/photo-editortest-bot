import { api, db, InputFile } from 'sdk';
import { eq } from 'sdk/db';
import { probeEvents } from '../schema.js';
import { createPhotoProbe } from '../lib/photo-probe.js';

async function setStatus(key, status) {
  const result = await db.update(probeEvents).set({ status })
    .where(eq(probeEvents.key, key)).run();
  if (result.rowsAffected !== 1) throw new Error('probe_state_missing');
}

export default createPhotoProbe({
  api,
  InputFile,
  async claim(event) {
    const rows = await db.insert(probeEvents)
      .values({ ...event, status: 'started', createdAt: Date.now() })
      .onConflictDoNothing({ target: probeEvents.key })
      .returning({ key: probeEvents.key }).run();
    return rows.length === 1;
  },
  finish: (key, status = 'delivered') => setStatus(key, status),
  review: key => setStatus(key, 'review'),
});
