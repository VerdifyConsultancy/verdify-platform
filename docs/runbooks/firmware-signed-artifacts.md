# Signed firmware artifacts

The suspended `verdify-firmware-builder` compiles and archives one exact Git
revision. It cannot reach the controller. Its build initContainer has the
existing compile-time Wi-Fi/API/OTA settings, but no signing key. A separate
cosign container signs the archived `release.json`; only that container mounts
`verdify-firmware-signing`, whose encrypted custody belongs to agents KSOPS.
The public key is pinned in this product's `cosign.pub` ConfigMap input.

The independent suspended `verdify-firmware-flasher` has no Git, Wi-Fi, API or
signing credential. It stages a private copy of the reviewed artifact, verifies
the cosign bundle with the pinned key, checks all binary hashes and the exact
reviewed manifest digest, and defaults to verification only. Its only permitted
network destination is controller TCP/3232. Neither template opens TCP/6053.

Prepare and inspect Jobs before submission:

```sh
python3 scripts/firmware-release-job.py build --revision FULL_SHA --name JOB_NAME
python3 scripts/firmware-release-job.py verify --revision FULL_SHA --expected MANIFEST_SHA256 --name JOB_NAME
```

Add `--submit` for an approved run. The helper binds the same full revision in
both build containers. Archives use the full source revision as their directory
name and cannot overwrite an existing directory. A failed partial archive stays
preserved for diagnosis; do not remove or overwrite it to manufacture a receipt.

After a successful build, review `/artifacts/FULL_SHA/release.json` and record
its SHA256. A verification-only Job exercises staging, signature and hash
verification without device contact. The manifest binds the source repository,
revision, firmware version, immutable compiler image and all four build
artifacts. `release.sigstore.json` is a standard cosign v3 bundle with a P-256
signature. Private builds use an empty signing-service configuration: nothing
is uploaded to a public transparency log or timestamp service. The trusted key
comes independently from Git, never from the bundle.

Only a separately authorized attended firmware release may select action
`flash`. Complete the existing firmware preflight and retain rollback/bake
evidence first. The flasher uploads the verified private binary without
recompiling; the operator then performs the existing database-backed version
and sensor-health checks. A software signature is not device-side enforcement:
signed-app verification/Secure Boot remains deferred under DR-48. The existing
OTA password, physical qualification and network-owner ACL acceptance remain
separate requirements. This pipeline does not rotate credentials or promote a
new last-good image automatically.
