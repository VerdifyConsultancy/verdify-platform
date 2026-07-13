const CONTROL_RE = /[\u0000-\u001f\u007f]/u;
const ENVIRONMENT_KEY_RE = /^[A-Z][A-Z0-9_]{0,127}$/u;
const RESOURCE_NAME_RE = /^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$/u;
const BUCKET_RE = /^[a-z0-9](?:[a-z0-9.-]{1,61}[a-z0-9])$/u;
const PREFIX_SEGMENT_RE = /^[A-Za-z0-9](?:[A-Za-z0-9._=-]{0,254})$/u;
const MAX_PREFIX_BYTES = 1024;

export const OCCURRENCE_STORE_BINDING_INVENTORY_CONTRACT =
    "verdify.lab-occurrence-store-binding-name-inventory";
export const OCCURRENCE_STORE_BINDING_READINESS_CONTRACT =
    "verdify.lab-occurrence-store-binding-readiness";

export const REQUIRED_OBJECT_STORE_ENVIRONMENT_KEY_NAMES = Object.freeze([
    "LAB_OCCURRENCE_STORE",
    "LAB_S3_ENDPOINT_URL",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_DEFAULT_REGION",
]);

const CONFIG_MAP_KEY_NAMES = Object.freeze([
    "LAB_OCCURRENCE_STORE",
    "LAB_S3_ENDPOINT_URL",
    "AWS_DEFAULT_REGION",
]);
const SECRET_KEY_NAMES = Object.freeze([
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
]);

export const OCCURRENCE_STORE_BINDING_RESOURCE_NAMES = Object.freeze({
    configMap: "verdify-lab-occurrence-store-metadata",
    readerSecret: "verdify-lab-occurrence-store-reader",
    writerSecret: "verdify-lab-occurrence-store-writer",
});

export const OCCURRENCE_STORE_BINDING_RESOURCE_KEY_NAMES = Object.freeze({
    configMap: CONFIG_MAP_KEY_NAMES,
    readerSecret: SECRET_KEY_NAMES,
    writerSecret: SECRET_KEY_NAMES,
});

function exactKeys(value, keys) {
    return (
        value !== null &&
        typeof value === "object" &&
        !Array.isArray(value) &&
        Object.keys(value).join("\0") === keys.join("\0")
    );
}

function validateExactNameInventory(value, expected, label, pattern) {
    if (!Array.isArray(value) || value.length === 0) {
        throw new Error(`${label} is not a name inventory`);
    }
    for (const name of value) {
        if (
            typeof name !== "string" ||
            name.length === 0 ||
            CONTROL_RE.test(name) ||
            !pattern.test(name)
        ) {
            throw new Error(`${label} contains an invalid name`);
        }
    }
    if (new Set(value).size !== value.length) {
        throw new Error(`${label} contains duplicate names`);
    }
    const actual = new Set(value);
    if (
        expected.some((name) => !actual.has(name)) ||
        value.some((name) => !expected.includes(name))
    ) {
        throw new Error(`${label} has missing or extra names`);
    }
    return [...expected];
}

function validateResource(resource, role) {
    if (!exactKeys(resource, ["name", "keyNames"])) {
        throw new Error(`occurrence-store ${role} inventory is not closed`);
    }
    const expectedName = OCCURRENCE_STORE_BINDING_RESOURCE_NAMES[role];
    if (
        typeof resource.name !== "string" ||
        CONTROL_RE.test(resource.name) ||
        !RESOURCE_NAME_RE.test(resource.name) ||
        resource.name !== expectedName
    ) {
        throw new Error(`occurrence-store ${role} resource name is invalid`);
    }
    const keyNames = validateExactNameInventory(
        resource.keyNames,
        OCCURRENCE_STORE_BINDING_RESOURCE_KEY_NAMES[role],
        `occurrence-store ${role} key-name inventory`,
        ENVIRONMENT_KEY_RE,
    );
    return { name: expectedName, keyNames };
}

function validBucket(bucket) {
    return (
        BUCKET_RE.test(bucket) &&
        !bucket.includes("..") &&
        !bucket.includes(".-") &&
        !bucket.includes("-.") &&
        !/^\d{1,3}(?:\.\d{1,3}){3}$/u.test(bucket)
    );
}

function validPrefix(prefix) {
    return (
        typeof prefix === "string" &&
        prefix.length > 0 &&
        !CONTROL_RE.test(prefix) &&
        Buffer.byteLength(prefix) <= MAX_PREFIX_BYTES &&
        !prefix.startsWith("/") &&
        !prefix.endsWith("/") &&
        !prefix.includes("\\") &&
        prefix
            .split("/")
            .every(
                (segment) =>
                    PREFIX_SEGMENT_RE.test(segment) &&
                    segment !== "." &&
                    segment !== "..",
            )
    );
}

function validateStoreLocationMetadata(metadata) {
    if (!exactKeys(metadata, ["kind", "bucket", "prefix"])) {
        throw new Error("occurrence-store location metadata is not closed");
    }
    if (
        metadata.kind !== "s3" ||
        typeof metadata.bucket !== "string" ||
        CONTROL_RE.test(metadata.bucket) ||
        !validBucket(metadata.bucket) ||
        !validPrefix(metadata.prefix)
    ) {
        throw new Error("occurrence-store location metadata is invalid");
    }
}

function freezeReadiness(readiness) {
    for (const resource of Object.values(readiness.resources)) {
        Object.freeze(resource.keyNames);
        Object.freeze(resource);
    }
    Object.freeze(readiness.resources);
    Object.freeze(readiness.environmentKeyNames);
    return Object.freeze(readiness);
}

/**
 * Enumerate an environment-like object's own enumerable key names without
 * accessing any corresponding value. This is deliberately the only supported
 * bridge from an environment object into the source-only readiness contract.
 */
export function enumerateObjectStoreBindingEnvironmentKeyNames(environment) {
    if (
        environment === null ||
        (typeof environment !== "object" && typeof environment !== "function")
    ) {
        throw new Error("object-store environment is not key-enumerable");
    }
    return Object.freeze(
        validateExactNameInventory(
            Object.keys(environment),
            REQUIRED_OBJECT_STORE_ENVIRONMENT_KEY_NAMES,
            "object-store environment key-name inventory",
            ENVIRONMENT_KEY_RE,
        ),
    );
}

export function validateOccurrenceStoreBindingInventory(inventory) {
    if (
        !exactKeys(inventory, [
            "contract",
            "schemaVersion",
            "environmentKeyNames",
            "resources",
            "storeLocationMetadata",
        ]) ||
        inventory.contract !== OCCURRENCE_STORE_BINDING_INVENTORY_CONTRACT ||
        inventory.schemaVersion !== 1
    ) {
        throw new Error(
            "occurrence-store binding inventory does not use the closed v1 contract",
        );
    }
    const environmentKeyNames = validateExactNameInventory(
        inventory.environmentKeyNames,
        REQUIRED_OBJECT_STORE_ENVIRONMENT_KEY_NAMES,
        "object-store environment key-name inventory",
        ENVIRONMENT_KEY_RE,
    );
    if (
        !exactKeys(inventory.resources, [
            "configMap",
            "readerSecret",
            "writerSecret",
        ])
    ) {
        throw new Error("occurrence-store resource-name inventory is not closed");
    }
    const resources = {
        configMap: validateResource(inventory.resources.configMap, "configMap"),
        readerSecret: validateResource(
            inventory.resources.readerSecret,
            "readerSecret",
        ),
        writerSecret: validateResource(
            inventory.resources.writerSecret,
            "writerSecret",
        ),
    };
    if (resources.readerSecret.name === resources.writerSecret.name) {
        throw new Error(
            "occurrence-store reader and writer Secret names must be separate",
        );
    }
    validateStoreLocationMetadata(inventory.storeLocationMetadata);

    return freezeReadiness({
        contract: OCCURRENCE_STORE_BINDING_READINESS_CONTRACT,
        schemaVersion: 1,
        status: "inventory-valid",
        environmentKeyNames,
        resources: {
            configMap: {
                name: resources.configMap.name,
                keyNames: resources.configMap.keyNames,
                status: "declared-by-name",
            },
            readerSecret: {
                name: resources.readerSecret.name,
                keyNames: resources.readerSecret.keyNames,
                status: "declared-by-name",
            },
            writerSecret: {
                name: resources.writerSecret.name,
                keyNames: resources.writerSecret.keyNames,
                status: "declared-by-name",
            },
        },
        storeLocationMetadataStatus: "valid-sanitized-metadata",
        authorityStatus: "source-only",
    });
}
