import { table, integer, text } from 'sdk/db';

// Transport probe metadata only: no images, prompts, keys or production ledger.
export const probeEvents = table('photo_probe_events', {
  key: text('event_key').notNull().primaryKey(),
  userId: integer('user_id').notNull(),
  messageId: integer('message_id').notNull(),
  status: text('status').notNull(),
  createdAt: integer('created_at').notNull(),
});
