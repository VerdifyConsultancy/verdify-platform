import { createHash } from "node:crypto";
import { constants as fsConstants, open, realpath } from "node:fs/promises";
import path from "node:path";
import { inflateSync } from "node:zlib";

const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const MAX_FILE_BYTES = 32 * 1024 * 1024;
const MAX_PIXELS = 25_000_000;
const MAX_DECODED_BYTES = 128 * 1024 * 1024;
const MAX_TOTAL_CHUNKS = 4096;
const MAX_IDAT_CHUNKS = 2048;

const CRC_TABLE = Array.from({ length: 256 }, (_, value) => {
  let crc = value;
  for (let bit = 0; bit < 8; bit += 1) crc = (crc & 1) === 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1;
  return crc >>> 0;
});

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function assertContained(root, target) {
  const relative = path.relative(root, target);
  if (relative === "" || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error("image candidate is outside its declared root");
  }
}

async function readBoundedImage(root, relative) {
  if (
    typeof relative !== "string"
    || relative.length === 0
    || relative.length > 1024
    || relative.includes("\\")
    || path.posix.normalize(relative) !== relative
    || relative.startsWith("/")
    || relative === ".."
    || relative.startsWith("../")
  ) {
    throw new Error("image candidate has an invalid relative path");
  }
  const canonicalRoot = await realpath(root);
  const target = path.join(canonicalRoot, ...relative.split("/"));
  assertContained(canonicalRoot, target);
  if ((await realpath(path.dirname(target))) !== path.dirname(target)) {
    throw new Error("image candidate parent resolves through a link");
  }
  let handle;
  try {
    handle = await open(target, fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW);
  } catch {
    throw new Error("image candidate cannot be opened without following links");
  }
  try {
    const metadata = await handle.stat({ bigint: true });
    if (!metadata.isFile() || metadata.nlink !== 1n) {
      throw new Error("image candidate must be a single-link regular file");
    }
    const size = Number(metadata.size);
    if (!Number.isSafeInteger(size) || size <= 0 || size > MAX_FILE_BYTES) {
      throw new Error("image candidate is outside the byte limit");
    }
    const bytes = await handle.readFile();
    if (bytes.length !== size) throw new Error("image candidate changed while being read");
    const after = await handle.stat({ bigint: true });
    if (after.dev !== metadata.dev || after.ino !== metadata.ino || after.size !== metadata.size || after.nlink !== 1n) {
      throw new Error("image candidate changed while being read");
    }
    return { bytes, target };
  } finally {
    await handle.close();
  }
}

function validateBitDepth(colorType, bitDepth) {
  return bitDepth === 8 && (colorType === 2 || colorType === 6);
}

function paeth(left, up, upperLeft) {
  const estimate = left + up - upperLeft;
  const leftDistance = Math.abs(estimate - left);
  const upDistance = Math.abs(estimate - up);
  const upperLeftDistance = Math.abs(estimate - upperLeft);
  if (leftDistance <= upDistance && leftDistance <= upperLeftDistance) return left;
  if (upDistance <= upperLeftDistance) return up;
  return upperLeft;
}

function decodeScanlines(inflated, width, height, bitsPerPixel) {
  const rowBytes = Math.ceil((width * bitsPerPixel) / 8);
  const expectedBytes = (rowBytes + 1) * height;
  if (expectedBytes > MAX_DECODED_BYTES || inflated.length !== expectedBytes) {
    throw new Error("PNG decoded byte count is invalid");
  }
  const bytesPerPixel = Math.max(1, Math.ceil(bitsPerPixel / 8));
  const decoded = Buffer.alloc(rowBytes * height);
  let inputOffset = 0;
  for (let row = 0; row < height; row += 1) {
    const filter = inflated[inputOffset];
    inputOffset += 1;
    if (filter > 4) throw new Error("PNG scanline uses an invalid filter");
    const outputOffset = row * rowBytes;
    const priorOffset = outputOffset - rowBytes;
    for (let column = 0; column < rowBytes; column += 1) {
      const encoded = inflated[inputOffset + column];
      const left = column >= bytesPerPixel ? decoded[outputOffset + column - bytesPerPixel] : 0;
      const up = row > 0 ? decoded[priorOffset + column] : 0;
      const upperLeft = row > 0 && column >= bytesPerPixel
        ? decoded[priorOffset + column - bytesPerPixel]
        : 0;
      let predictor = 0;
      if (filter === 1) predictor = left;
      if (filter === 2) predictor = up;
      if (filter === 3) predictor = Math.floor((left + up) / 2);
      if (filter === 4) predictor = paeth(left, up, upperLeft);
      decoded[outputOffset + column] = (encoded + predictor) & 0xff;
    }
    inputOffset += rowBytes;
  }
  return decoded;
}

export function decodePng(bytes) {
  if (!Buffer.isBuffer(bytes) || bytes.length < 57 || !bytes.subarray(0, 8).equals(PNG_SIGNATURE)) {
    throw new Error("image candidate is not a PNG");
  }
  let offset = 8;
  let header = null;
  let sawEnd = false;
  let sawImageData = false;
  let imageDataClosed = false;
  let totalChunks = 0;
  let imageDataChunks = 0;
  let compressedLength = 0;
  const compressed = [];
  while (offset < bytes.length) {
    if (offset + 12 > bytes.length) throw new Error("PNG chunk framing is truncated");
    totalChunks += 1;
    if (totalChunks > MAX_TOTAL_CHUNKS) throw new Error("PNG exceeds its total chunk-count limit");
    const length = bytes.readUInt32BE(offset);
    if (length > MAX_FILE_BYTES || offset + 12 + length > bytes.length) {
      throw new Error("PNG chunk exceeds its bounds");
    }
    const typeBytes = bytes.subarray(offset + 4, offset + 8);
    const type = typeBytes.toString("ascii");
    if (!/^[A-Za-z]{4}$/.test(type)) throw new Error("PNG chunk type is invalid");
    const data = bytes.subarray(offset + 8, offset + 8 + length);
    const expectedCrc = bytes.readUInt32BE(offset + 8 + length);
    if (crc32(Buffer.concat([typeBytes, data])) !== expectedCrc) throw new Error("PNG chunk checksum failed");
    if (header === null && type !== "IHDR") throw new Error("PNG header is not first");
    if (type === "IHDR") {
      if (header !== null || length !== 13) throw new Error("PNG header is invalid");
      const width = data.readUInt32BE(0);
      const height = data.readUInt32BE(4);
      const bitDepth = data[8];
      const colorType = data[9];
      if (
        width === 0
        || height === 0
        || width * height > MAX_PIXELS
        || !validateBitDepth(colorType, bitDepth)
        || data[10] !== 0
        || data[11] !== 0
        || data[12] !== 0
      ) {
        throw new Error("PNG header uses unsupported dimensions or encoding");
      }
      header = { width, height, bitDepth, colorType };
    } else if (!["IDAT", "IEND"].includes(type)) {
      throw new Error("PNG contains an unsupported metadata or structural chunk");
    } else if (type === "IDAT") {
      if (sawEnd || imageDataClosed) throw new Error("PNG image data chunks are not contiguous");
      if (length === 0) throw new Error("PNG image data chunk is empty");
      imageDataChunks += 1;
      if (imageDataChunks > MAX_IDAT_CHUNKS) throw new Error("PNG exceeds its image-data chunk-count limit");
      compressedLength += length;
      if (compressedLength > MAX_FILE_BYTES) throw new Error("PNG image data exceeds its compressed-byte limit");
      sawImageData = true;
      compressed.push(data);
    } else if (type === "IEND") {
      if (length !== 0 || sawEnd) throw new Error("PNG end chunk is invalid");
      sawEnd = true;
      offset += 12;
      break;
    }
    if (sawImageData && type !== "IDAT") imageDataClosed = true;
    offset += 12 + length;
  }
  if (header === null || !sawEnd || compressed.length === 0 || offset !== bytes.length) {
    throw new Error("PNG structure is incomplete or has trailing bytes");
  }
  const channels = header.colorType === 2 ? 3 : 4;
  const bitsPerPixel = channels * header.bitDepth;
  const rowBytes = Math.ceil((header.width * bitsPerPixel) / 8);
  const expectedInflated = (rowBytes + 1) * header.height;
  if (expectedInflated > MAX_DECODED_BYTES) throw new Error("PNG decoded byte count exceeds its bound");
  const compressedBytes = Buffer.concat(compressed, compressedLength);
  let result;
  try {
    result = inflateSync(compressedBytes, { info: true, maxOutputLength: expectedInflated });
  } catch {
    throw new Error("PNG image data cannot be decoded within bounds");
  }
  if (result.engine.bytesWritten !== compressedBytes.length) {
    throw new Error("PNG image data contains trailing or concatenated streams");
  }
  const inflated = result.buffer;
  const decoded = decodeScanlines(inflated, header.width, header.height, bitsPerPixel);
  return {
    ...header,
    mediaType: "image/png",
    decodedBytes: decoded.length,
    decodedSha256: createHash("sha256").update(decoded).digest("hex"),
  };
}

export async function validatePngFile(root, relative) {
  const { bytes, target } = await readBoundedImage(root, relative);
  const decoded = decodePng(bytes);
  return {
    ...decoded,
    bytes: bytes.length,
    sha256: createHash("sha256").update(bytes).digest("hex"),
    sourcePath: target,
  };
}

export const limits = {
  maxFileBytes: MAX_FILE_BYTES,
  maxPixels: MAX_PIXELS,
  maxDecodedBytes: MAX_DECODED_BYTES,
  maxTotalChunks: MAX_TOTAL_CHUNKS,
  maxIdatChunks: MAX_IDAT_CHUNKS,
};
