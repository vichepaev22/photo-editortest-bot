'use strict';
// Optional recovery after a confirmed server rejection; never a network retry.
window.ObrazPhotoUpload = {
  async jpeg(file) {
    const bytes = new Uint8Array(await file.slice(0, 64).arrayBuffer());
    const word = (a, b) => String.fromCharCode(...bytes.slice(a, b));
    const jpeg = bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255;
    const png = word(1, 4) === 'PNG' && bytes[0] === 137;
    const webp = word(0, 4) === 'RIFF' && word(8, 12) === 'WEBP';
    const heif = word(4, 8) === 'ftyp' && ['heic','heix','hevc','hevx','mif1','msf1'].includes(word(8, 12));
    if (!jpeg && !png && !webp && !heif) throw new Error('unsupported_image');
    const url = URL.createObjectURL(file), image = new Image(), canvas = document.createElement('canvas');
    let timer;
    try {
      await new Promise((resolve, reject) => {
        timer = setTimeout(() => reject(new Error('decode_timeout')), 8000);
        image.onload = resolve;
        image.onerror = () => reject(new Error('decode_failed'));
        image.src = url;
      });
      clearTimeout(timer);
      const width = image.naturalWidth, height = image.naturalHeight;
      if (!width || !height || width * height > 64_000_000) throw new Error('image_resolution_limit');
      const scale = Math.min(1, 2048 / Math.max(width, height));
      canvas.width = Math.max(1, Math.round(width * scale));
      canvas.height = Math.max(1, Math.round(height * scale));
      const context = canvas.getContext('2d');
      if (!context) throw new Error('canvas_unavailable');
      context.fillStyle = '#fff'; context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise((resolve, reject) => {
        timer = setTimeout(() => reject(new Error('encode_timeout')), 8000);
        canvas.toBlob(resolve, 'image/jpeg', .92);
      });
      if (!blob || !blob.size || blob.type !== 'image/jpeg' || blob.size > 10_000_000) throw new Error('encode_failed');
      return blob;
    } finally {
      clearTimeout(timer); image.onload = image.onerror = null; image.src = '';
      URL.revokeObjectURL(url); canvas.width = canvas.height = 0;
    }
  }
};
