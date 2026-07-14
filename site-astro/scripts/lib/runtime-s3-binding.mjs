import {
    createOccurrenceReleaseStore,
    parseOccurrenceReleaseStoreLocation,
} from "./occurrence-release-store.mjs";
import {
    createSiteReleaseStore,
    parseSiteReleaseStoreLocation,
} from "./site-release-store.mjs";

export const RUNTIME_S3_ENDPOINT_URL = "https://s3-hdd.vallery.net";
export const RUNTIME_S3_REGION = "garage";

const CONTROL_RE = /[\u0000-\u001f\u007f]/u;
const MAX_ACCESS_KEY_BYTES = 256;
const MAX_SECRET_ACCESS_KEY_BYTES = 1024;

function ownEnvironmentString(environment, name, maximumBytes) {
    if (
        environment === null ||
        (typeof environment !== "object" && typeof environment !== "function")
    ) {
        throw new Error("runtime S3 environment is required");
    }
    let present;
    let value;
    try {
        present = Object.prototype.hasOwnProperty.call(environment, name);
        if (present) value = environment[name];
    } catch {
        throw new Error(`runtime S3 ${name} is invalid`);
    }
    if (!present)
        throw new Error(`runtime S3 ${name} is required`);
    if (
        typeof value !== "string" ||
        value.length === 0 ||
        value.trim() !== value ||
        Buffer.byteLength(value) > maximumBytes ||
        CONTROL_RE.test(value)
    ) {
        throw new Error(`runtime S3 ${name} is invalid`);
    }
    return value;
}

function runtimeS3Environment(environment) {
    const endpoint = ownEnvironmentString(
        environment,
        "LAB_S3_ENDPOINT_URL",
        256,
    );
    if (endpoint !== RUNTIME_S3_ENDPOINT_URL)
        throw new Error("runtime S3 LAB_S3_ENDPOINT_URL is invalid");
    const region = ownEnvironmentString(
        environment,
        "AWS_DEFAULT_REGION",
        64,
    );
    if (region !== RUNTIME_S3_REGION)
        throw new Error("runtime S3 AWS_DEFAULT_REGION is invalid");
    const accessKeyId = ownEnvironmentString(
        environment,
        "AWS_ACCESS_KEY_ID",
        MAX_ACCESS_KEY_BYTES,
    );
    const secretAccessKey = ownEnvironmentString(
        environment,
        "AWS_SECRET_ACCESS_KEY",
        MAX_SECRET_ACCESS_KEY_BYTES,
    );
    return Object.freeze({
        LAB_S3_ENDPOINT_URL: endpoint,
        AWS_DEFAULT_REGION: region,
        AWS_ACCESS_KEY_ID: accessKeyId,
        AWS_SECRET_ACCESS_KEY: secretAccessKey,
    });
}

function runtimeS3ClientConfig(environment) {
    const binding = runtimeS3Environment(environment);
    return Object.freeze({
        endpoint: RUNTIME_S3_ENDPOINT_URL,
        region: RUNTIME_S3_REGION,
        forcePathStyle: true,
        credentials: Object.freeze({
            accessKeyId: binding.AWS_ACCESS_KEY_ID,
            secretAccessKey: binding.AWS_SECRET_ACCESS_KEY,
        }),
    });
}

function factoryOptions(options) {
    if (
        options === null ||
        typeof options !== "object" ||
        Array.isArray(options)
    ) {
        throw new Error("runtime S3 store options are invalid");
    }
    const create = options.create ?? false;
    if (typeof create !== "boolean")
        throw new Error("runtime S3 store create option is invalid");
    if (
        options.clientFactory !== undefined &&
        typeof options.clientFactory !== "function"
    ) {
        throw new Error("runtime S3 client factory is invalid");
    }
    return {
        create,
        environment: options.environment,
        clientFactory: options.clientFactory,
    };
}

async function createInitializedStore({
    storeRoot,
    options,
    accessMode,
    parseLocation,
    createStore,
}) {
    const selected = factoryOptions(options);
    const location = parseLocation(storeRoot);
    if (location.kind === "local") {
        const store = createStore(storeRoot);
        return store.initialize({ create: selected.create });
    }
    const store = createStore(storeRoot, {
        accessMode,
        clientConfig: runtimeS3ClientConfig(selected.environment),
        clientFactory: selected.clientFactory,
    });
    return store.initialize();
}

export async function createSiteReleaseReaderStore(storeRoot, options = {}) {
    return createInitializedStore({
        storeRoot,
        options,
        accessMode: "reader",
        parseLocation: parseSiteReleaseStoreLocation,
        createStore: createSiteReleaseStore,
    });
}

export async function createSiteReleaseWriterStore(storeRoot, options = {}) {
    return createInitializedStore({
        storeRoot,
        options,
        accessMode: "writer",
        parseLocation: parseSiteReleaseStoreLocation,
        createStore: createSiteReleaseStore,
    });
}

export async function createOccurrenceReleaseReaderStore(
    storeRoot,
    options = {},
) {
    return createInitializedStore({
        storeRoot,
        options,
        accessMode: "reader",
        parseLocation: parseOccurrenceReleaseStoreLocation,
        createStore: createOccurrenceReleaseStore,
    });
}

export async function createOccurrenceReleaseWriterStore(
    storeRoot,
    options = {},
) {
    return createInitializedStore({
        storeRoot,
        options,
        accessMode: "writer",
        parseLocation: parseOccurrenceReleaseStoreLocation,
        createStore: createOccurrenceReleaseStore,
    });
}

export function siteReleaseCliEnvironment(storeRoot, options = {}) {
    const selected = factoryOptions(options);
    const location = parseSiteReleaseStoreLocation(storeRoot);
    if (location.kind === "local") return Object.freeze({});
    return runtimeS3Environment(selected.environment);
}
