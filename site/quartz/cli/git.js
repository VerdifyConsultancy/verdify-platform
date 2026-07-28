import { spawnSync } from "child_process"

const NETWORK_GIT_CONFIG = ["-c", "credential.helper=", "-c", "http.extraHeader="]

function validateRemoteName(name) {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(name)) {
    throw new Error("Git remote name is invalid")
  }
}

function validateGitHubHttpsUrl(url) {
  let parsed
  try {
    parsed = new URL(url)
  } catch {
    throw new Error("Git remote must be credential-free GitHub HTTPS")
  }

  if (
    parsed.protocol !== "https:" ||
    parsed.hostname.toLowerCase() !== "github.com" ||
    parsed.port !== "" ||
    parsed.username !== "" ||
    parsed.password !== "" ||
    parsed.search !== "" ||
    parsed.hash !== "" ||
    !/^\/[^/\s]+\/[^/\s]+$/.test(parsed.pathname)
  ) {
    throw new Error("Git remote must be credential-free GitHub HTTPS")
  }
}

function configKeyIsUnsafe(scope, key, remoteName) {
  if (
    scope === "global" &&
    ["core.askpass", "credential.helper", "credential.username"].includes(key)
  ) {
    // These keys are installed/reset by the uniform Agent Fleet runtime. The
    // command-level empty helper below still wins; GIT_ASKPASS remains the
    // operation-time rotating credential handle.
    return false
  }
  if (key === "core.askpass" || key.startsWith("credential.") || key.startsWith("url.")) {
    return true
  }
  if (key.startsWith("http.")) {
    // Any HTTP override can change authentication or transport semantics. In
    // particular curloptResolve plus weakened TLS verification can redirect a
    // validated github.com URL before the Fleet-owned askpass is invoked.
    return true
  }
  return (
    key === `remote.${remoteName.toLowerCase()}.proxy` ||
    key === `remote.${remoteName.toLowerCase()}.proxyauthmethod`
  )
}

function rejectAuthBearingGitConfig(remoteName) {
  const result = spawnSync(
    "git",
    [
      "config",
      "--includes",
      "--show-scope",
      "--name-only",
      "--get-regexp",
      "^(credential\\.|core\\.askpass$|http\\.|url\\.|remote\\..*\\.(proxy|proxyauthmethod)$)",
    ],
    { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] },
  )
  if (result.error) throw result.error
  if (result.status !== 0 && result.status !== 1) {
    throw new Error("Unable to validate Git authentication configuration")
  }
  const unsafe = result.stdout
    .trim()
    .split(/\r?\n/)
    .filter(Boolean)
    .some((line) => {
      const [scope, key] = line.toLowerCase().split(/\s+/, 2)
      return !scope || !key || configKeyIsUnsafe(scope, key, remoteName)
    })
  if (unsafe) throw new Error("Git authentication configuration is not runtime-owned")
}

function remoteUrl(name, push = false) {
  validateRemoteName(name)
  rejectAuthBearingGitConfig(name)
  const args = ["remote", "get-url"]
  if (push) args.push("--push")
  args.push("--all")
  args.push(name)
  const result = spawnSync("git", args, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "ignore"],
  })
  if (result.error) throw result.error
  const urls = result.status === 0 ? result.stdout.trim().split(/\r?\n/).filter(Boolean) : []
  if (urls.length !== 1) throw new Error(`Git remote ${name} is unavailable or ambiguous`)
  validateGitHubHttpsUrl(urls[0])
  return urls[0]
}

function runGit(args, operation) {
  const result = spawnSync("git", [...NETWORK_GIT_CONFIG, ...args], {
    stdio: "inherit",
    env: {
      ...process.env,
      // Preserve the Fleet-owned GIT_ASKPASS handle, but prevent Git from
      // falling through to an ambient SSH askpass or an interactive prompt.
      SSH_ASKPASS: "/bin/false",
      GIT_TERMINAL_PROMPT: "0",
    },
  })
  if (result.error) throw result.error
  if (result.status !== 0) {
    throw new Error(`${operation} failed with status ${result.status ?? "unknown"}`)
  }
}

export function ensureCredentialFreeRemote(name, url) {
  validateRemoteName(name)
  validateGitHubHttpsUrl(url)
  const current = spawnSync("git", ["remote", "get-url", name], { stdio: "ignore" })
  if (current.error) throw current.error

  if (current.status === 0) {
    runGit(["remote", "set-url", name, url], `git remote set-url ${name}`)
  } else {
    runGit(["remote", "add", name, url], `git remote add ${name}`)
  }
}

export function gitPull(origin, branch) {
  const url = remoteUrl(origin)
  const flags = ["--no-rebase", "--autostash", "-s", "recursive", "-X", "ours", "--no-edit"]
  runGit(["pull", ...flags, url, branch], `git pull ${origin}/${branch}`)
}

export function gitPublicPull(origin, branch) {
  const url = remoteUrl(origin)
  const flags = ["--no-rebase", "--autostash", "-s", "recursive", "-X", "ours", "--no-edit"]
  const result = spawnSync("git", [...NETWORK_GIT_CONFIG, "pull", ...flags, url, branch], {
    stdio: "inherit",
    env: {
      PATH: process.env.PATH ?? "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
      HOME: "/tmp",
      GIT_CONFIG_NOSYSTEM: "1",
      GIT_CONFIG_GLOBAL: "/dev/null",
      GIT_ASKPASS: "/bin/false",
      SSH_ASKPASS: "/bin/false",
      GIT_TERMINAL_PROMPT: "0",
    },
  })
  if (result.error) throw result.error
  if (result.status !== 0) {
    throw new Error(`git pull ${origin}/${branch} failed with status ${result.status ?? "unknown"}`)
  }
}

export function gitPush(origin, branch) {
  const url = remoteUrl(origin, true)
  runGit(["push", "-uf", url, branch], `git push ${origin}/${branch}`)
}
