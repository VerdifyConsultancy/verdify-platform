import { isCancel, outro } from "@clack/prompts"
import { styleText } from "util"
import { contentCacheFolder } from "./constants.js"
import fs from "fs"

export function escapePath(fp) {
  return fp
    .replace(/\\ /g, " ") // unescape spaces
    .replace(/^"(.*)"$/, "$1")
    .replace(/^'(.*)'$/, "$1")
    .trim()
}

export function exitIfCancel(val) {
  if (isCancel(val)) {
    outro(styleText("red", "Exiting"))
    process.exit(0)
  } else {
    return val
  }
}

export async function stashContentFolder(contentFolder) {
  await fs.promises.rm(contentCacheFolder, { force: true, recursive: true })
  await fs.promises.cp(contentFolder, contentCacheFolder, {
    force: true,
    recursive: true,
    verbatimSymlinks: true,
    preserveTimestamps: true,
  })
  await fs.promises.rm(contentFolder, { force: true, recursive: true })
}

export async function popContentFolder(contentFolder) {
  await fs.promises.rm(contentFolder, { force: true, recursive: true })
  await fs.promises.cp(contentCacheFolder, contentFolder, {
    force: true,
    recursive: true,
    verbatimSymlinks: true,
    preserveTimestamps: true,
  })
  await fs.promises.rm(contentCacheFolder, { force: true, recursive: true })
}
