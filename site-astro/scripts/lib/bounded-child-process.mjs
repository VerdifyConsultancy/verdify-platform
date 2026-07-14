import { spawn } from "node:child_process";

const ENVIRONMENT_NAME_RE = /^[A-Z_][A-Z0-9_]*$/u;
const CONTROL_RE = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u;

function positiveInteger(value, label, maximum) {
    if (!Number.isSafeInteger(value) || value < 1 || value > maximum) {
        throw new Error(`${label} is invalid`);
    }
    return value;
}

function safeText(value, label, maximum = 4096) {
    if (
        typeof value !== "string" ||
        value.length === 0 ||
        value.length > maximum ||
        CONTROL_RE.test(value)
    ) {
        throw new Error(`${label} is invalid`);
    }
    return value;
}

function exactEnvironment(value) {
    if (
        value === null ||
        typeof value !== "object" ||
        Array.isArray(value) ||
        Object.getPrototypeOf(value) !== Object.prototype
    ) {
        throw new Error("bounded child environment is invalid");
    }
    const result = {};
    for (const [name, raw] of Object.entries(value)) {
        if (
            !ENVIRONMENT_NAME_RE.test(name) ||
            typeof raw !== "string" ||
            raw.length > 16 * 1024 ||
            raw.includes("\u0000")
        ) {
            throw new Error("bounded child environment is invalid");
        }
        result[name] = raw;
    }
    return result;
}

function signalProcessTree(child, signal) {
    if (!Number.isSafeInteger(child.pid) || child.pid < 1) return;
    try {
        process.kill(-child.pid, signal);
    } catch (error) {
        if (error.code !== "ESRCH") {
            try {
                child.kill(signal);
            } catch (fallbackError) {
                if (fallbackError.code !== "ESRCH") throw fallbackError;
            }
        }
    }
}

/**
 * Run one argv-only child in its own process group. A timeout, parent
 * termination signal, or output overflow sends TERM to the whole group and
 * follows with KILL after one bounded grace interval.
 */
export async function runBoundedChildProcess({
    label,
    executable,
    arguments: rawArguments = [],
    cwd,
    environment,
    timeoutMs = 15 * 60 * 1000,
    terminationGraceMs = 10 * 1000,
    maximumOutputBytes = 2 * 1024 * 1024,
    forwardOutput = true,
} = {}) {
    safeText(label, "bounded child label", 256);
    safeText(executable, "bounded child executable");
    safeText(cwd, "bounded child working directory");
    if (
        !Array.isArray(rawArguments) ||
        rawArguments.length > 256 ||
        rawArguments.some(
            (value) =>
                typeof value !== "string" ||
                value.length > 16 * 1024 ||
                value.includes("\u0000"),
        )
    ) {
        throw new Error("bounded child arguments are invalid");
    }
    const childEnvironment = exactEnvironment(environment);
    positiveInteger(timeoutMs, "bounded child timeout", 60 * 60 * 1000);
    positiveInteger(
        terminationGraceMs,
        "bounded child termination grace",
        60 * 1000,
    );
    positiveInteger(
        maximumOutputBytes,
        "bounded child output limit",
        16 * 1024 * 1024,
    );
    if (typeof forwardOutput !== "boolean") {
        throw new Error("bounded child output forwarding flag is invalid");
    }

    return new Promise((resolve, reject) => {
        const child = spawn(executable, rawArguments, {
            cwd,
            env: childEnvironment,
            detached: true,
            shell: false,
            stdio: ["ignore", "pipe", "pipe"],
        });
        const chunks = { stdout: [], stderr: [] };
        const sizes = { stdout: 0, stderr: 0 };
        let terminalError = null;
        let killTimer = null;
        let timeout = null;
        let settled = false;
        let closeObserved = false;
        let killDeadlineReached = false;

        const finishTerminated = () => {
            if (
                terminalError !== null &&
                closeObserved &&
                killDeadlineReached
            ) {
                finish(terminalError);
            }
        };

        const terminate = (error) => {
            if (terminalError !== null || settled) return;
            terminalError = error;
            signalProcessTree(child, "SIGTERM");
            killTimer = setTimeout(() => {
                killTimer = null;
                signalProcessTree(child, "SIGKILL");
                killDeadlineReached = true;
                finishTerminated();
            }, terminationGraceMs);
        };
        const onParentTerm = () =>
            terminate(new Error(`${label} interrupted by SIGTERM`));
        const onParentInt = () =>
            terminate(new Error(`${label} interrupted by SIGINT`));
        const append = (stream, chunk) => {
            const bytes = Buffer.from(chunk);
            sizes[stream] += bytes.length;
            if (sizes[stream] > maximumOutputBytes) {
                terminate(new Error(`${label} exceeded its output limit`));
                return;
            }
            chunks[stream].push(bytes);
            if (forwardOutput) {
                const destination =
                    stream === "stdout" ? process.stdout : process.stderr;
                destination.write(bytes);
            }
        };
        const cleanup = () => {
            if (timeout !== null) clearTimeout(timeout);
            if (killTimer !== null) clearTimeout(killTimer);
            process.off("SIGTERM", onParentTerm);
            process.off("SIGINT", onParentInt);
        };
        const finish = (error, result = null) => {
            if (settled) return;
            settled = true;
            cleanup();
            if (error !== null) reject(error);
            else resolve(result);
        };

        child.stdout.on("data", (chunk) => append("stdout", chunk));
        child.stderr.on("data", (chunk) => append("stderr", chunk));
        child.once("error", (error) =>
            finish(new Error(`${label} could not start: ${error.message}`)),
        );
        child.once("close", (code, signal) => {
            if (terminalError !== null) {
                closeObserved = true;
                finishTerminated();
                return;
            }
            if (code !== 0) {
                const disposition = signal
                    ? `signal ${signal}`
                    : `exit code ${code}`;
                finish(new Error(`${label} failed with ${disposition}`));
                return;
            }
            finish(null, {
                stdout: Buffer.concat(chunks.stdout, sizes.stdout),
                stderr: Buffer.concat(chunks.stderr, sizes.stderr),
            });
        });
        process.once("SIGTERM", onParentTerm);
        process.once("SIGINT", onParentInt);
        timeout = setTimeout(
            () => terminate(new Error(`${label} exceeded ${timeoutMs}ms`)),
            timeoutMs,
        );
    });
}

export const boundedChildProcessContract = Object.freeze({
    contract: "verdify.bounded-child-process",
    schemaVersion: 1,
    processGroup: true,
    termination: Object.freeze(["SIGTERM", "SIGKILL"]),
});
