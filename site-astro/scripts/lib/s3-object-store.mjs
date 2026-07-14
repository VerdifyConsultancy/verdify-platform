import path from "node:path";

import {
    GetObjectCommand,
    ListObjectsV2Command,
    PutObjectCommand,
    S3Client,
} from "@aws-sdk/client-s3";

const CONTROL_RE = /[\u0000-\u001f\u007f]/u;
const SCHEME_RE = /^[A-Za-z][A-Za-z0-9+.-]*:/u;
const BUCKET_RE = /^[a-z0-9](?:[a-z0-9.-]{1,61}[a-z0-9])$/u;
const KEY_SEGMENT_RE = /^[A-Za-z0-9](?:[A-Za-z0-9._=-]{0,254})$/u;
const ETAG_RE = /^"[^"\\\u0000-\u001f\u007f]{1,512}"$/u;
const MAX_KEY_BYTES = 1024;
const MAX_LIST_PAGES = 100;
const MAX_CONTINUATION_TOKEN_BYTES = 4096;
const ACCESS_MODES = new Set(["reader", "writer"]);

function validBucket(bucket) {
    return (
        BUCKET_RE.test(bucket) &&
        !bucket.includes("..") &&
        !bucket.includes(".-") &&
        !bucket.includes("-.") &&
        !/^\d{1,3}(?:\.\d{1,3}){3}$/u.test(bucket)
    );
}

function validKeySegment(segment) {
    return KEY_SEGMENT_RE.test(segment) && segment !== "." && segment !== "..";
}

function validateKeyPrefix(prefix, label) {
    if (
        typeof prefix !== "string" ||
        prefix.length === 0 ||
        Buffer.byteLength(prefix) > MAX_KEY_BYTES ||
        prefix.startsWith("/") ||
        prefix.endsWith("/") ||
        prefix.split("/").some((segment) => !validKeySegment(segment))
    ) {
        throw new Error(`${label} is invalid`);
    }
    return prefix;
}

function validateRelativeObjectKey(value, { allowTrailingSlash = false } = {}) {
    if (
        typeof value !== "string" ||
        value.length === 0 ||
        CONTROL_RE.test(value) ||
        value.includes("\\")
    ) {
        throw new Error("S3 object key is invalid");
    }
    const candidate =
        allowTrailingSlash && value.endsWith("/") ? value.slice(0, -1) : value;
    validateKeyPrefix(candidate, "S3 object key");
    if (Buffer.byteLength(value) > MAX_KEY_BYTES)
        throw new Error("S3 object key is invalid");
    return value;
}

function validateETag(value, label = "S3 entity tag") {
    if (typeof value !== "string" || !ETAG_RE.test(value))
        throw new Error(`${label} is invalid`);
    return value;
}

function isMissing(error) {
    return (
        error?.name === "NoSuchKey" ||
        error?.name === "NotFound" ||
        error?.$metadata?.httpStatusCode === 404
    );
}

function isPrecondition(error) {
    return (
        error?.name === "PreconditionFailed" ||
        error?.name === "ConditionalRequestConflict" ||
        [409, 412].includes(error?.$metadata?.httpStatusCode)
    );
}

async function releaseResponseBody(body) {
    if (body === undefined || body === null) return;
    if (typeof body.destroy === "function") {
        await body.destroy();
        return;
    }
    if (typeof body.cancel === "function") await body.cancel();
}

async function boundedBody(body, maximumBytes, contentLength, label) {
    if (!Number.isSafeInteger(maximumBytes) || maximumBytes < 1)
        throw new Error(`${label} byte limit is invalid`);
    if (
        contentLength !== undefined &&
        (!Number.isSafeInteger(contentLength) ||
            contentLength < 0 ||
            contentLength > maximumBytes)
    ) {
        await releaseResponseBody(body).catch(() => {});
        throw new Error(`${label} exceeds its byte limit`);
    }
    if (body === undefined || body === null)
        throw new Error(`${label} has no response body`);
    const chunks = [];
    let total = 0;
    const append = (chunk) => {
        if (typeof chunk === "string") chunk = Buffer.from(chunk);
        if (!ArrayBuffer.isView(chunk))
            throw new Error(`${label} returned a non-byte chunk`);
        const bytes = Buffer.from(
            chunk.buffer,
            chunk.byteOffset,
            chunk.byteLength,
        );
        total += bytes.length;
        if (total > maximumBytes)
            throw new Error(`${label} exceeds its byte limit`);
        chunks.push(bytes);
    };
    if (typeof body === "string" || ArrayBuffer.isView(body)) {
        append(body);
    } else if (typeof body[Symbol.asyncIterator] === "function") {
        for await (const chunk of body) append(chunk);
    } else {
        throw new Error(`${label} body is not a bounded byte stream`);
    }
    if (contentLength !== undefined && total !== contentLength)
        throw new Error(`${label} byte count changed during read`);
    return Buffer.concat(chunks, total);
}

export function parseSiteReleaseStoreLocation(value) {
    if (
        typeof value !== "string" ||
        value.length === 0 ||
        value.length > 4096 ||
        CONTROL_RE.test(value)
    ) {
        throw new Error("site release store location is invalid");
    }
    if (value.startsWith("s3://")) {
        const remainder = value.slice("s3://".length);
        const separator = remainder.indexOf("/");
        if (separator < 1 || separator === remainder.length - 1)
            throw new Error("S3 site release store URI is invalid");
        const bucket = remainder.slice(0, separator);
        const prefix = remainder.slice(separator + 1);
        if (!validBucket(bucket))
            throw new Error("S3 site release store bucket is invalid");
        validateKeyPrefix(prefix, "S3 site release store key prefix");
        return Object.freeze({ kind: "s3", bucket, prefix });
    }
    if (
        SCHEME_RE.test(value) ||
        value.startsWith("//") ||
        value.includes("\\")
    ) {
        throw new Error(
            "site release store location is neither a local path nor a valid S3 URI",
        );
    }
    return Object.freeze({ kind: "local", root: path.resolve(value) });
}

export class S3ObjectStore {
    constructor({
        bucket,
        prefix,
        accessMode = "writer",
        client = null,
        clientConfig = {},
        clientFactory = (config) => new S3Client(config),
    }) {
        if (!validBucket(bucket))
            throw new Error("S3 object-store bucket is invalid");
        this.bucket = bucket;
        this.prefix = validateKeyPrefix(prefix, "S3 object-store key prefix");
        if (
            clientConfig === null ||
            typeof clientConfig !== "object" ||
            Array.isArray(clientConfig)
        ) {
            throw new Error("S3 client configuration is invalid");
        }
        if (client !== null && typeof client.send !== "function")
            throw new Error("S3 client is invalid");
        if (typeof clientFactory !== "function")
            throw new Error("S3 client factory is invalid");
        if (!ACCESS_MODES.has(accessMode))
            throw new Error("S3 object-store access mode is invalid");
        this.accessMode = accessMode;
        this.client = client;
        this.clientConfig = { ...clientConfig };
        this.clientFactory = clientFactory;
    }

    async initialize() {
        if (this.client === null)
            this.client = this.clientFactory({ ...this.clientConfig });
        if (!this.client || typeof this.client.send !== "function")
            throw new Error("S3 client factory returned an invalid client");
        return this;
    }

    objectKey(relative) {
        validateRelativeObjectKey(relative);
        const key = `${this.prefix}/${relative}`;
        if (Buffer.byteLength(key) > MAX_KEY_BYTES)
            throw new Error("S3 object key exceeds its byte limit");
        return key;
    }

    async read(
        relative,
        { maximumBytes, label = "S3 object", missing = false } = {},
    ) {
        const Key = this.objectKey(relative);
        let result;
        try {
            result = await this.client.send(
                new GetObjectCommand({ Bucket: this.bucket, Key }),
            );
        } catch (error) {
            if (missing && isMissing(error)) return null;
            throw error;
        }
        const bytes = await boundedBody(
            result.Body,
            maximumBytes,
            result.ContentLength,
            label,
        );
        return { bytes, etag: validateETag(result.ETag) };
    }

    async putIfAbsent(
        relative,
        bytes,
        { contentType = "application/octet-stream" } = {},
    ) {
        if (this.accessMode !== "writer")
            throw new Error("S3 object store is not configured for writes");
        if (!Buffer.isBuffer(bytes) || bytes.length < 1)
            throw new Error("S3 object body is invalid");
        const Key = this.objectKey(relative);
        try {
            const result = await this.client.send(
                new PutObjectCommand({
                    Bucket: this.bucket,
                    Key,
                    Body: bytes,
                    ContentLength: bytes.length,
                    ContentType: contentType,
                    IfNoneMatch: "*",
                }),
            );
            return { written: true, etag: validateETag(result.ETag) };
        } catch (error) {
            if (isPrecondition(error)) return { written: false, etag: null };
            throw error;
        }
    }

    async putIfMatch(
        relative,
        bytes,
        expectedETag,
        { contentType = "application/json" } = {},
    ) {
        if (this.accessMode !== "writer")
            throw new Error("S3 object store is not configured for writes");
        if (!Buffer.isBuffer(bytes) || bytes.length < 1)
            throw new Error("S3 object body is invalid");
        validateETag(expectedETag, "S3 compare-and-swap entity tag");
        const Key = this.objectKey(relative);
        try {
            const result = await this.client.send(
                new PutObjectCommand({
                    Bucket: this.bucket,
                    Key,
                    Body: bytes,
                    ContentLength: bytes.length,
                    ContentType: contentType,
                    IfMatch: expectedETag,
                }),
            );
            return { written: true, etag: validateETag(result.ETag) };
        } catch (error) {
            if (isPrecondition(error)) return { written: false, etag: null };
            throw error;
        }
    }

    async list(relativePrefix, { maximumObjects = 1000 } = {}) {
        validateRelativeObjectKey(relativePrefix, { allowTrailingSlash: true });
        if (
            !Number.isSafeInteger(maximumObjects) ||
            maximumObjects < 1 ||
            maximumObjects > 1000
        ) {
            throw new Error("S3 object listing limit is invalid");
        }
        const Prefix = `${this.prefix}/${relativePrefix}`;
        if (Buffer.byteLength(Prefix) > MAX_KEY_BYTES)
            throw new Error("S3 object key exceeds its byte limit");
        const keys = [];
        const seenKeys = new Set();
        const seenTokens = new Set();
        let ContinuationToken;
        for (let page = 0; page < MAX_LIST_PAGES; page += 1) {
            const result = await this.client.send(
                new ListObjectsV2Command({
                    Bucket: this.bucket,
                    Prefix,
                    MaxKeys: 1000,
                    ...(ContinuationToken === undefined
                        ? {}
                        : { ContinuationToken }),
                }),
            );
            const contents = result.Contents ?? [];
            if (!Array.isArray(contents))
                throw new Error("S3 object listing is invalid");
            for (const entry of contents) {
                if (
                    entry === null ||
                    typeof entry !== "object" ||
                    typeof entry.Key !== "string" ||
                    !entry.Key.startsWith(Prefix) ||
                    entry.Key.length === Prefix.length ||
                    seenKeys.has(entry.Key)
                ) {
                    throw new Error("S3 object listing membership is invalid");
                }
                const relative = entry.Key.slice(`${this.prefix}/`.length);
                try {
                    validateRelativeObjectKey(relative);
                } catch {
                    throw new Error("S3 object listing membership is invalid");
                }
                if (Buffer.byteLength(entry.Key) > MAX_KEY_BYTES)
                    throw new Error("S3 object listing membership is invalid");
                seenKeys.add(entry.Key);
                keys.push(relative);
                if (keys.length > maximumObjects)
                    throw new Error(
                        "S3 object listing exceeds its membership limit",
                    );
            }
            if (result.IsTruncated !== true) {
                if (
                    result.IsTruncated !== false &&
                    result.IsTruncated !== undefined
                )
                    throw new Error("S3 object listing pagination is invalid");
                return keys;
            }
            const token = result.NextContinuationToken;
            if (
                typeof token !== "string" ||
                token.length === 0 ||
                CONTROL_RE.test(token) ||
                Buffer.byteLength(token) > MAX_CONTINUATION_TOKEN_BYTES ||
                seenTokens.has(token)
            ) {
                throw new Error("S3 object listing continuation is invalid");
            }
            seenTokens.add(token);
            ContinuationToken = token;
        }
        throw new Error("S3 object listing exceeds its page limit");
    }
}
