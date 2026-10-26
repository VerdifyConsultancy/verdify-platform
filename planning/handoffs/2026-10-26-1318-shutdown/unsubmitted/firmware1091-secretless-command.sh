python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile

VERSION = '2026.6.5'
IMAGE = 'ghcr.io/esphome/esphome@sha256:ffe23402d169fc9b8ff29fc3c9fc13b3c47e8a53726fce4569e2280bd534c84c'
TARGETS = ('greenhouse', 'greenhouse-recovery')
COMPILERS = frozenset(prefix + '-' + suffix
    for prefix in ('xtensa-esp-elf', 'xtensa-esp32-elf', 'xtensa-esp32s2-elf', 'xtensa-esp32s3-elf')
    for suffix in ('as', 'g++', 'gcc', 'ld'))
DUMMY = 'wifi_ssid: CI-NONDEPLOYABLE\nwifi_password: CI-NONDEPLOYABLE-NOT-A-WLAN-PASSWORD\nota_password: CI-NONDEPLOYABLE-NOT-AN-OTA-PASSWORD\napi_encryption_key: AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compiler_provenance(packages_dir):
    # Capture actual downloaded bytes before private scratch is removed.
    # These are the same sixteen compiler filenames qualified in the pinned
    # ARM64 image; host-specific hashes must be observed afresh on amd64.
    base = packages_dir.resolve()
    packages = []
    observed = []
    for manifest in sorted(packages_dir.glob('*/package.json')):
        if not manifest.resolve().is_relative_to(base):
            raise RuntimeError('provenance path escapes private packages')
        metadata = json.loads(manifest.read_text())
        binaries = []
        for binary in sorted((manifest.parent / 'bin').glob('*')):
            if binary.name not in COMPILERS:
                continue
            if not binary.resolve().is_relative_to(base):
                raise RuntimeError('provenance path escapes private packages')
            if binary.is_file():
                binaries.append({'name': binary.name, 'bytes': binary.stat().st_size,
                                 'sha256': digest(binary)})
                observed.append(binary.name)
        packages.append({'name': metadata['name'], 'version': metadata['version'],
            'package_manifest_sha256': digest(manifest), 'compiler_binaries': binaries})
    if not packages:
        raise RuntimeError('compiled toolchain package provenance absent')
    if len({p['name'] for p in packages}) != len(packages):
        raise RuntimeError('duplicate compiled toolchain package identity')
    if sorted(observed) != sorted(COMPILERS):
        raise RuntimeError('compiler binary provenance incomplete or duplicated')
    return {'schema': 'verdify-secretless-container-toolchain/v1',
            'actual_host': platform.platform(), 'packages': packages}


def source_files(root):
    # Read only tracked firmware blobs; never follow checkout symlinks, an ignored
    # secrets.yaml, .esphome cache, operator artifacts or a caller-selected file.
    names = subprocess.check_output(['git', 'ls-tree', '-rz', 'HEAD', 'firmware'], cwd=root).split(b'\0')
    result = {}
    for entry in filter(None, names):
        meta, name = entry.split(b'\t', 1)
        mode, kind, oid = meta.split()
        path = name.decode()
        if mode not in (b'100644', b'100755') or kind != b'blob':
            raise RuntimeError('non-regular tracked firmware input refused')
        if any(x in path.split('/') for x in ('secrets.yaml', '.esphome', 'artifacts')):
            raise RuntimeError('secret/cache/artifact tracked input refused')
        result[path] = subprocess.check_output(['git', 'cat-file', 'blob', oid.decode()], cwd=root)
    for target in TARGETS:
        if 'firmware/' + target + '.yaml' not in result:
            raise RuntimeError('declared firmware target missing')
    # The original script applies a guarded correction to exactly this ESPHome.
    if b'2026.6.5' not in result.get('firmware/build_api_batch_patch.py', b''):
        raise RuntimeError('firmware/toolchain contract mismatch')
    return result


def main():
    root = Path.cwd()
    source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    bound = os.environ.get('FLEET_CI_HEAD_REVISION')
    if bound and bound != source:
        raise RuntimeError('checkout revision differs from native requested source')
    files = source_files(root)
    report = {'schema': 'verdify.secretless-firmware/v1', 'source': source,
              'toolchain_image': IMAGE, 'esphome_version': VERSION,
              'deployable': False, 'physical_qualification': False,
              'credentials': 'public compile-only dummy values', 'targets': []}
    with tempfile.TemporaryDirectory(prefix='verdify-firmware-ci-', dir=root.parent) as work:
        work = Path(work)
        home = work / 'home'; home.mkdir()
        env = {'PATH': os.environ['PATH'], 'HOME': str(home), 'TMPDIR': str(work),
               'PLATFORMIO_CORE_DIR': str(work / 'platformio'),
               'ESPHOME_DEFAULT_COMPILE_PROCESS_LIMIT': '2',
               'SOURCE_DATE_EPOCH': subprocess.check_output(['git', 'show', '-s', '--format=%ct', 'HEAD'], cwd=root, text=True).strip()}
        import importlib.metadata
        if importlib.metadata.version('esphome') != VERSION:
            raise RuntimeError('wrong ESPHome version; no compile performed')
        for target in TARGETS:
            tree = work / target
            for name, content in files.items():
                dest = tree / name; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(content)
            firmware = tree / 'firmware'
            (firmware / 'secrets.yaml').write_text(DUMMY)
            config = firmware / (target + '.yaml')
            cmd = ['esphome', '-s', 'fw_version', 'CI-NONDEPLOYABLE-' + source[:12]]
            subprocess.run(cmd + ['config', str(config)], cwd=tree, env=env, check=True)
            subprocess.run(cmd + ['compile', str(config)], cwd=tree, env=env, check=True)
            binary = firmware / '.esphome/build/greenhouse/.pioenvs/greenhouse/firmware.ota.bin'
            if not binary.is_file() or not binary.stat().st_size:
                raise RuntimeError('compiler succeeded without OTA-format binary')
            report['targets'].append({'target': target, 'config_sha256': digest(config),
                'source_manifest_sha256': hashlib.sha256(json.dumps({k: hashlib.sha256(v).hexdigest() for k, v in sorted(files.items())}, sort_keys=True).encode()).hexdigest(),
                'generated_cpp_sha256': digest(firmware / '.esphome/build/greenhouse/src/main.cpp'),
                'platformio_ini_sha256': digest(firmware / '.esphome/build/greenhouse/platformio.ini'),
                'binary_sha256': digest(binary), 'binary_bytes': binary.stat().st_size,
                'result': 'compiled', 'deployable': False})
            if target == 'greenhouse':
                # Real generated API service regression: config-valid C++ must
                # fail actual compilation. Reuse only this private dummy build.
                text = config.read_text()
                if text.count('  services:\n') != 1:
                    raise RuntimeError('native generated service insertion point changed')
                config.write_text(text.replace('  services:\n',
                    '  services:\n    - service: ci_generated_service_failure\n'
                    '      then:\n        - lambda: |-\n'
                    '            CI_NONDEPLOYABLE_GENERATED_SERVICE_MUST_NOT_EXIST();\n', 1))
                subprocess.run(cmd + ['config', str(config)], cwd=tree, env=env, check=True)
                negative = subprocess.run(cmd + ['compile', str(config)], cwd=tree, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                if negative.returncode == 0 or b'CI_NONDEPLOYABLE_GENERATED_SERVICE_MUST_NOT_EXIST' not in negative.stdout:
                    raise RuntimeError('broken generated C++ did not fail compilation as expected')
                report['generated_cpp_negative'] = {'result': 'expected_compile_failure', 'exit_code': negative.returncode}
        report['compiler_provenance'] = compiler_provenance(work / 'platformio/packages')
        report['platformio_packages'] = {p['name']: p['version'] for p in report['compiler_provenance']['packages']}
        print('FIRMWARE_SECRETLESS_RECEIPT=' + json.dumps(report, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
PY
