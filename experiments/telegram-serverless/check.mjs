import assert from 'node:assert/strict';
import { createPhotoProbe, MAX_BYTES } from './tgcloud/lib/photo-probe.js';

// Synthetic one-pixel PNG: no owner photo, external SDK, network or credentials.
const bytes = new Uint8Array(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6ioAAAAASUVORK5CYII=', 'base64'));
const events = new Map();
let downloads = 0;
let replies = 0;
const deliveries = [];
const probe = createPhotoProbe({
  api: {
    async sendMessage() { replies++; },
    async sendChatAction() {},
    async getFileContent() { downloads++; return bytes; },
    async sendDocument(value) { deliveries.push(value); },
  },
  InputFile: class { constructor(content, name, options) { Object.assign(this, { content, name, options }); } },
  async claim(event) {
    if (events.has(event.key)) return false;
    events.set(event.key, { ...event, status: 'started' });
    return true;
  },
  async finish(key, status = 'delivered') { events.get(key).status = status; },
  async review(key) { events.get(key).status = 'review'; },
});
const message = { from: { id: 1 }, chat: { id: 1, type: 'private' }, message_id: 10,
  document: { file_id: 'synthetic', file_size: bytes.length, mime_type: 'image/png' } };

// Concurrent duplicate delivery should claim only one event before any download.
const outcomes = await Promise.all([probe(message), probe(message)]);
assert.deepEqual(outcomes.map(x => x.status).sort(), ['delivered', 'duplicate']);
assert.equal(downloads, 1);
assert.equal(deliveries.length, 1);
assert.deepEqual(deliveries[0].document.content, bytes);
assert.equal(deliveries[0].chat_id, 1);
assert.equal(events.get('1:10').status, 'delivered');
const oversize = { ...message, message_id: 11,
  document: { ...message.document, file_size: MAX_BYTES + 1 } };
assert.equal((await probe(oversize)).status, 'rejected');
assert.equal((await probe(oversize)).status, 'duplicate');
assert.equal(replies, 1);
assert.equal((await probe({ ...message, chat: { id: 2, type: 'private' } })).status, 'ignored');
assert.equal(downloads, 1);
assert.equal(events.size, 2);
console.log(JSON.stringify({ status: 'passed', scope: 'offline transport core with injected SDK/DB', delivered: 1,
  duplicate: 2, oversize: 'rejected once before download', foreign_chat: 'ignored', network_calls: 0, image_api_calls: 0 }));
