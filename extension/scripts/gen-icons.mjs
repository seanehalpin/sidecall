// Writes placeholder icons to public/icon/<size>.png (a design pass is pending).
// A rounded indigo square with a white "call" dot. No dependencies.
import { mkdirSync, writeFileSync } from 'node:fs';
import { deflateSync } from 'node:zlib';

const crcTable = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
const crc32 = (buf) => {
  let c = 0xffffffff;
  for (const b of buf) c = crcTable[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
};
const chunk = (type, data) => {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
};

function icon(size) {
  const rows = [];
  const r = size * 0.22; // corner radius
  for (let y = 0; y < size; y++) {
    const row = [0];
    for (let x = 0; x < size; x++) {
      const px = x + 0.5, py = y + 0.5;
      const dx = Math.max(r - px, 0, px - (size - r));
      const dy = Math.max(r - py, 0, py - (size - r));
      const inSquare = dx * dx + dy * dy <= r * r;
      const cx = px - size / 2, cy = py - size / 2;
      const inDot = cx * cx + cy * cy <= (size * 0.2) ** 2;
      if (!inSquare) row.push(0, 0, 0, 0);
      else if (inDot) row.push(255, 255, 255, 255);
      else row.push(79, 70, 229, 255);
    }
    rows.push(Buffer.from(row));
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr.set([8, 6, 0, 0, 0], 8);
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(Buffer.concat(rows))),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

mkdirSync(new URL('../public/icon/', import.meta.url), { recursive: true });
for (const size of [16, 32, 48, 128]) {
  writeFileSync(new URL(`../public/icon/${size}.png`, import.meta.url), icon(size));
}
console.log('wrote public/icon/{16,32,48,128}.png');
