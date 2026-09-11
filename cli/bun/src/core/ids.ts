import { randomBytes } from "node:crypto";

export function uuidV7(nowMilliseconds = Date.now(), random = randomBytes(10)): string {
  const bytes = new Uint8Array(16);
  let timestamp = BigInt(Math.trunc(nowMilliseconds));
  for (let index = 5; index >= 0; index -= 1) {
    bytes[index] = Number(timestamp & 0xffn);
    timestamp >>= 8n;
  }
  bytes[6] = 0x70 | (random[0]! & 0x0f);
  bytes[7] = random[1]!;
  bytes[8] = 0x80 | (random[2]! & 0x3f);
  for (let index = 9; index < 16; index += 1) bytes[index] = random[index - 6]!;
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
