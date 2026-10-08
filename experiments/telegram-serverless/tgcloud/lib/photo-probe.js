export const MAX_BYTES = 10_000_000;

function positiveId(value) {
  return Number.isSafeInteger(value) && value > 0;
}

function imageType(bytes) {
  if (bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) {
    return { name: 'Проверка.jpg', type: 'image/jpeg' };
  }
  const png = [137, 80, 78, 71, 13, 10, 26, 10];
  if (png.every((value, index) => bytes[index] === value)) {
    return { name: 'Проверка.png', type: 'image/png' };
  }
  return null;
}

// SDK bindings live in handlers/message.js; dependency injection keeps this probe
// checkable offline without importing a Node package into the V8 runtime.
export function createPhotoProbe({ api, InputFile, claim, finish, review }) {
  return async function probe(message) {
    if (message?.chat?.type !== 'private' || !positiveId(message.from?.id)
      || message.chat.id !== message.from.id || !positiveId(message.message_id)) {
      return { status: 'ignored' };
    }
    const chatId = message.chat.id;
    const key = `${message.from.id}:${message.message_id}`;
    if (!await claim({ key, userId: message.from.id, messageId: message.message_id })) {
      return { status: 'duplicate' };
    }
    try {
      const source = message.document ?? message.photo?.[message.photo.length - 1];
      if (!source) {
        await api.sendMessage({
          chat_id: chatId,
          text: 'Это тест передачи фото, без генерации и списания попыток. Отправьте тестовый JPEG или PNG до 10 MB; верну тот же файл. Для проверки используйте фото без личных данных.',
        });
        await finish(key, 'instruction');
        return { status: 'instruction' };
      }
      const size = source.file_size;
      if (typeof source.file_id !== 'string' || !source.file_id
        || (size !== undefined && (!Number.isSafeInteger(size) || size < 0 || size > MAX_BYTES))
        || (message.document && !['image/jpeg', 'image/png'].includes(source.mime_type))) {
        await api.sendMessage({ chat_id: chatId, text: 'Для теста нужен JPEG или PNG до 10 MB.' });
        await finish(key, 'rejected');
        return { status: 'rejected' };
      }
      // Chat activity is best effort and cannot prevent delivery.
      try { await api.sendChatAction({ chat_id: chatId, action: 'upload_photo' }); } catch {}
      const bytes = await api.getFileContent(source.file_id);
      const file = bytes instanceof Uint8Array && bytes.length <= MAX_BYTES ? imageType(bytes) : null;
      if (!file) throw new Error('invalid_test_image');
      await api.sendDocument({
        chat_id: chatId,
        document: new InputFile(bytes, file.name, { type: file.type }),
        caption: 'Тест передачи завершён. Это исходный файл; OpenAI не вызывался, попытки не списывались.',
      });
      await finish(key);
      return { status: 'delivered' };
    } catch {
      // A send may have succeeded before a timeout. Never blindly re-send it.
      // If the DB also fails, the existing started row remains unresolved.
      try { await review(key); } catch {}
      try {
        await api.sendMessage({ chat_id: chatId, text: 'Не удалось подтвердить завершение теста. Повторную отправку этого сообщения нужно проверить вручную.' });
      } catch {}
      return { status: 'review' };
    }
  };
}
