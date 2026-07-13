import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import test from "node:test";

import {
    enumerateObjectStoreBindingEnvironmentKeyNames,
    OCCURRENCE_STORE_BINDING_INVENTORY_CONTRACT,
    OCCURRENCE_STORE_BINDING_RESOURCE_KEY_NAMES,
    OCCURRENCE_STORE_BINDING_RESOURCE_NAMES,
    REQUIRED_OBJECT_STORE_ENVIRONMENT_KEY_NAMES,
    validateOccurrenceStoreBindingInventory,
} from "../scripts/lib/object-store-binding-readiness.mjs";

const execFileAsync = promisify(execFile);
const SITE_ROOT = path.resolve(import.meta.dirname, "..");
const CLI = path.join(
    SITE_ROOT,
    "scripts/check-object-store-binding-readiness.mjs",
);

function canonicalBytes(value) {
    return `${JSON.stringify(value, null, 2)}\n`;
}

function validInventory() {
    return {
        contract: OCCURRENCE_STORE_BINDING_INVENTORY_CONTRACT,
        schemaVersion: 1,
        environmentKeyNames: [...REQUIRED_OBJECT_STORE_ENVIRONMENT_KEY_NAMES],
        resources: {
            configMap: {
                name: OCCURRENCE_STORE_BINDING_RESOURCE_NAMES.configMap,
                keyNames: [
                    ...OCCURRENCE_STORE_BINDING_RESOURCE_KEY_NAMES.configMap,
                ],
            },
            readerSecret: {
                name: OCCURRENCE_STORE_BINDING_RESOURCE_NAMES.readerSecret,
                keyNames: [
                    ...OCCURRENCE_STORE_BINDING_RESOURCE_KEY_NAMES.readerSecret,
                ],
            },
            writerSecret: {
                name: OCCURRENCE_STORE_BINDING_RESOURCE_NAMES.writerSecret,
                keyNames: [
                    ...OCCURRENCE_STORE_BINDING_RESOURCE_KEY_NAMES.writerSecret,
                ],
            },
        },
        storeLocationMetadata: {
            kind: "s3",
            bucket: "verdify-lab-occurrences",
            prefix: "lab-stage/occurrences",
        },
    };
}

test("binding readiness reports only the closed key and resource names plus statuses", () => {
    const readiness = validateOccurrenceStoreBindingInventory(validInventory());
    assert.deepEqual(readiness, {
        contract: "verdify.lab-occurrence-store-binding-readiness",
        schemaVersion: 1,
        status: "inventory-valid",
        environmentKeyNames: [
            "LAB_OCCURRENCE_STORE",
            "LAB_S3_ENDPOINT_URL",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_DEFAULT_REGION",
        ],
        resources: {
            configMap: {
                name: "verdify-lab-occurrence-store-metadata",
                keyNames: [
                    "LAB_OCCURRENCE_STORE",
                    "LAB_S3_ENDPOINT_URL",
                    "AWS_DEFAULT_REGION",
                ],
                status: "declared-by-name",
            },
            readerSecret: {
                name: "verdify-lab-occurrence-store-reader",
                keyNames: ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
                status: "declared-by-name",
            },
            writerSecret: {
                name: "verdify-lab-occurrence-store-writer",
                keyNames: ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
                status: "declared-by-name",
            },
        },
        storeLocationMetadataStatus: "valid-sanitized-metadata",
        authorityStatus: "source-only",
    });
    const output = canonicalBytes(readiness);
    assert.doesNotMatch(output, /verdify-lab-occurrences|lab-stage\/occurrences/u);
    assert.doesNotMatch(output, /s3:\/\/|https?:\/\//u);
    assert.equal(Object.isFrozen(readiness), true);
    assert.equal(Object.isFrozen(readiness.resources.writerSecret), true);
});

test("environment inspection performs own-key enumeration without any value read", () => {
    const target = {};
    for (const name of [...REQUIRED_OBJECT_STORE_ENVIRONMENT_KEY_NAMES].reverse()) {
        Object.defineProperty(target, name, {
            configurable: true,
            enumerable: true,
            get() {
                throw new Error("environment value getter must never run");
            },
        });
    }
    const traps = [];
    const environment = new Proxy(target, {
        ownKeys(value) {
            traps.push("ownKeys");
            return Reflect.ownKeys(value);
        },
        getOwnPropertyDescriptor(value, property) {
            traps.push("getOwnPropertyDescriptor");
            return Reflect.getOwnPropertyDescriptor(value, property);
        },
        get() {
            throw new Error("environment value access is forbidden");
        },
        has() {
            throw new Error("environment membership value access is forbidden");
        },
    });
    assert.deepEqual(enumerateObjectStoreBindingEnvironmentKeyNames(environment), [
        ...REQUIRED_OBJECT_STORE_ENVIRONMENT_KEY_NAMES,
    ]);
    assert.equal(traps.filter((name) => name === "ownKeys").length, 1);
    assert.deepEqual(
        [...new Set(traps)].sort(),
        ["getOwnPropertyDescriptor", "ownKeys"].sort(),
    );
});

test("environment key inventories reject missing, extra, duplicate, and control-character names", () => {
    const fixtures = [
        REQUIRED_OBJECT_STORE_ENVIRONMENT_KEY_NAMES.slice(1),
        [...REQUIRED_OBJECT_STORE_ENVIRONMENT_KEY_NAMES, "UNEXPECTED_KEY"],
        [
            ...REQUIRED_OBJECT_STORE_ENVIRONMENT_KEY_NAMES,
            REQUIRED_OBJECT_STORE_ENVIRONMENT_KEY_NAMES[0],
        ],
        [
            ...REQUIRED_OBJECT_STORE_ENVIRONMENT_KEY_NAMES.slice(0, -1),
            "AWS_DEFAULT_REGION\n",
        ],
    ];
    for (const environmentKeyNames of fixtures) {
        const inventory = validInventory();
        inventory.environmentKeyNames = environmentKeyNames;
        assert.throws(
            () => validateOccurrenceStoreBindingInventory(inventory),
            /name|missing|extra|duplicate/u,
        );
    }
});

test("resource inventories reject missing, extra, duplicate, control names, and legacy reuse", () => {
    const mutate = [
        (value) => {
            delete value.resources.readerSecret;
        },
        (value) => {
            value.resources.unexpected = value.resources.readerSecret;
        },
        (value) => {
            value.resources.readerSecret.keyNames.push(
                value.resources.readerSecret.keyNames[0],
            );
        },
        (value) => {
            value.resources.configMap.keyNames[0] = "LAB_OCCURRENCE_STORE\u0000";
        },
        (value) => {
            value.resources.writerSecret.name =
                "verdify-lab-occurrence-store-reader";
        },
        (value) => {
            value.resources.readerSecret.name = "verdify-lab-publisher-s3";
        },
    ];
    for (const change of mutate) {
        const inventory = validInventory();
        change(inventory);
        assert.throws(
            () => validateOccurrenceStoreBindingInventory(inventory),
            /closed|name|duplicate|separate/u,
        );
    }
});

test("store location metadata accepts only a closed sanitized S3 identity", () => {
    const invalid = [
        { kind: "local", bucket: "verdify-lab", prefix: "occurrences" },
        { kind: "s3", bucket: "Verdify-Lab", prefix: "occurrences" },
        { kind: "s3", bucket: "127.0.0.1", prefix: "occurrences" },
        { kind: "s3", bucket: "verdify-lab", prefix: "/occurrences" },
        { kind: "s3", bucket: "verdify-lab", prefix: "a/../occurrences" },
        { kind: "s3", bucket: "verdify-lab", prefix: "occurrences\n" },
        {
            kind: "s3",
            bucket: "verdify-lab",
            prefix: "occurrences",
            endpoint: "https://object-store.invalid",
        },
    ];
    for (const storeLocationMetadata of invalid) {
        const inventory = validInventory();
        inventory.storeLocationMetadata = storeLocationMetadata;
        assert.throws(
            () => validateOccurrenceStoreBindingInventory(inventory),
            /location metadata/u,
        );
    }
});

test("CLI consumes canonical name inventory rather than process environment values", async (t) => {
    const root = await mkdtemp(path.join(tmpdir(), "verdify-binding-names-"));
    t.after(() => rm(root, { recursive: true, force: true }));
    const inventoryPath = path.join(root, "inventory.json");
    await writeFile(inventoryPath, canonicalBytes(validInventory()), {
        mode: 0o600,
    });
    const { stdout, stderr } = await execFileAsync(
        process.execPath,
        [CLI, "--inventory", inventoryPath],
        { cwd: SITE_ROOT },
    );
    assert.equal(stderr, "");
    const readiness = JSON.parse(stdout);
    assert.equal(readiness.status, "inventory-valid");
    assert.equal(readiness.authorityStatus, "source-only");
    assert.doesNotMatch(stdout, /verdify-lab-occurrences|lab-stage\/occurrences/u);

    const source = await readFile(CLI, "utf8");
    assert.doesNotMatch(source, /process\.env/u);
    const librarySource = await readFile(
        path.join(
            SITE_ROOT,
            "scripts/lib/object-store-binding-readiness.mjs",
        ),
        "utf8",
    );
    assert.doesNotMatch(
        `${source}\n${librarySource}`,
        /node:(?:http|https|net|dns)|@aws-sdk|\bfetch\s*\(|\bkubectl\b|process\.env/u,
    );
});

test("CLI rejects noncanonical and value-bearing documents without reflecting values", async (t) => {
    const root = await mkdtemp(path.join(tmpdir(), "verdify-binding-invalid-"));
    t.after(() => rm(root, { recursive: true, force: true }));
    const noncanonical = path.join(root, "noncanonical.json");
    await writeFile(noncanonical, JSON.stringify(validInventory()));
    await assert.rejects(
        execFileAsync(process.execPath, [CLI, "--inventory", noncanonical], {
            cwd: SITE_ROOT,
        }),
        (error) => {
            assert.equal(error.stdout, "");
            assert.match(error.stderr, /not canonical JSON/u);
            return true;
        },
    );

    const valueBearing = validInventory();
    valueBearing.endpointValue = "https://must-not-be-reflected.invalid";
    const valueBearingPath = path.join(root, "value-bearing.json");
    await writeFile(valueBearingPath, canonicalBytes(valueBearing));
    await assert.rejects(
        execFileAsync(process.execPath, [CLI, "--inventory", valueBearingPath], {
            cwd: SITE_ROOT,
        }),
        (error) => {
            assert.equal(error.stdout, "");
            assert.match(error.stderr, /closed v1 contract/u);
            assert.doesNotMatch(error.stderr, /must-not-be-reflected/u);
            return true;
        },
    );
});
