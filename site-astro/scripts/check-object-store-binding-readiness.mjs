import { constants as fsConstants, open } from "node:fs/promises";
import path from "node:path";

import { validateOccurrenceStoreBindingInventory } from "./lib/object-store-binding-readiness.mjs";

const MAX_INVENTORY_BYTES = 64 * 1024;

function usage() {
    return "Usage: node scripts/check-object-store-binding-readiness.mjs --inventory INVENTORY.json";
}

async function readCanonicalInventory(file) {
    const absolute = path.resolve(file);
    const handle = await open(
        absolute,
        fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW,
    );
    let bytes;
    try {
        const before = await handle.stat({ bigint: true });
        if (
            !before.isFile() ||
            before.nlink !== 1n ||
            before.size < 1n ||
            before.size > BigInt(MAX_INVENTORY_BYTES)
        ) {
            throw new Error(
                "binding inventory is not a bounded single-link regular file",
            );
        }
        bytes = await handle.readFile();
        const after = await handle.stat({ bigint: true });
        if (
            after.dev !== before.dev ||
            after.ino !== before.ino ||
            after.size !== before.size ||
            after.nlink !== 1n
        ) {
            throw new Error("binding inventory changed while being read");
        }
    } finally {
        await handle.close();
    }
    let inventory;
    try {
        inventory = JSON.parse(bytes.toString("utf8"));
    } catch {
        throw new Error("binding inventory is not valid JSON");
    }
    if (`${JSON.stringify(inventory, null, 2)}\n` !== bytes.toString("utf8")) {
        throw new Error("binding inventory is not canonical JSON");
    }
    return inventory;
}

async function main() {
    const argv = process.argv.slice(2);
    if (argv.length !== 2 || argv[0] !== "--inventory" || !argv[1]) {
        throw new Error(usage());
    }
    const inventory = await readCanonicalInventory(argv[1]);
    const readiness = validateOccurrenceStoreBindingInventory(inventory);
    process.stdout.write(`${JSON.stringify(readiness, null, 2)}\n`);
}

main().catch((error) => {
    process.stderr.write(`object-store-binding-readiness: ${error.message}\n`);
    process.exitCode = 1;
});
