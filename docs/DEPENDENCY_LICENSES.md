# Dependency and license review

## Current boundary

- Runtime dependencies are declared in `requirements-runtime.txt`; build-only
  dependencies are in `requirements-build.txt`; WPF's direct NuGet dependency
  is in `ui/windows/Genie.Desktop/Genie.Desktop.csproj`.
- Laya source and checkpoint are independently pinned and remain shadow-only.
  Their source/model notices are recorded in `THIRD_PARTY_NOTICES.md`.
- NEDLE2 uses the `cactus-needle` package and a separately provisioned native
  runtime. Preserve both license notices in the installer/release package.
- The embedded Python and self-contained .NET publish each bring bundled
  notices that must be collected from the exact build outputs.
- Vendored `vendor/prime_rlm` is an active integration; its upstream license
  and source provenance must remain with that code. `vendor/strix-audit` is
  audit-only source and is not a product runtime dependency.

## Release blockers and required evidence

Several Python dependencies are not pinned to exact versions. No complete
resolved lockfile or release SBOM is present in the current source tree. The
current runtime package is therefore not a reproducible license inventory by
itself. Before redistribution, generate a package-version SBOM from the exact
embedded runtime, collect `pip show` license metadata and bundled native DLL
notices, and archive the output with the release manifest.

## Current embedded Python inventory (observed 2026-10-08)

This is the exact `importlib.metadata` inventory in the existing
`backend-dist/backend-runtime/python` tree, including each installed METADATA
License field. `UNSPECIFIED` means that field was blank; it is not a license
grant. Keep the release blocked on collecting authoritative per-wheel license
files for those rows. The full inventory is included here to make those gaps
visible rather than silently treating packages as cleared.

| Distribution | Version | Installed METADATA License | Review state |
|---|---:|---|---|
| PyYAML | 6.0.3 | MIT | metadata recorded |
| annotated-types | 0.8.0 | UNSPECIFIED | resolve wheel notice |
| anyio | 4.15.1 | UNSPECIFIED | resolve wheel notice |
| attrs | 26.1.0 | UNSPECIFIED | resolve wheel notice |
| av | 18.1.0 | UNSPECIFIED | review wheel and bundled FFmpeg notices |
| cactus-needle | 2.0.15 | Apache-2.0 | metadata recorded; preserve notice |
| certifi | 2026.7.22 | MPL-2.0 | metadata recorded; preserve notice |
| cffi | 2.1.1 | UNSPECIFIED | resolve wheel notice |
| charset-normalizer | 3.5.1 | MIT | metadata recorded |
| click | 8.5.0 | UNSPECIFIED | resolve wheel notice |
| colorama | 0.4.6 | UNSPECIFIED | resolve wheel notice |
| comtypes | 1.4.17 | UNSPECIFIED | resolve wheel notice |
| cryptography | 50.0.1 | UNSPECIFIED | resolve wheel notice and bundled OpenSSL notices |
| ctranslate2 | 4.8.2 | MIT | metadata recorded |
| distro | 1.9.0 | Apache-2.0 | metadata recorded |
| faster-whisper | 1.2.1 | MIT | metadata recorded |
| filelock | 4.0.1 | UNSPECIFIED | resolve wheel notice |
| flatbuffers | 25.12.19 | Apache 2.0 | metadata recorded |
| fsspec | 2026.9.0 | UNSPECIFIED | resolve wheel notice |
| google-auth | 2.58.0 | Apache 2.0 | metadata recorded |
| google-genai | 2.25.0 | UNSPECIFIED | upstream Apache-2.0 verified; preserve license file |
| h11 | 0.16.0 | MIT | metadata recorded |
| hf-xet | 1.6.0 | UNSPECIFIED | resolve wheel notice |
| httpcore | 1.0.9 | UNSPECIFIED | resolve wheel notice |
| httpx | 0.28.1 | BSD-3-Clause | metadata recorded |
| huggingface_hub | 1.32.0 | Apache-2.0 | metadata recorded |
| idna | 3.20 | UNSPECIFIED | resolve wheel notice |
| jsonschema | 4.26.0 | UNSPECIFIED | resolve wheel notice |
| jsonschema-specifications | 2025.9.1 | UNSPECIFIED | resolve wheel notice |
| numpy | 2.5.3 | UNSPECIFIED | resolve wheel notice and native notices |
| onnxruntime | 1.30.0 | MIT License | metadata recorded; preserve native notices |
| opencv-python-headless | 5.0.0.93 | Apache 2.0 | metadata recorded; review wheel notices |
| packaging | 26.3 | UNSPECIFIED | resolve wheel notice |
| Pillow | 12.3.0 | UNSPECIFIED | upstream MIT-CMU verified; preserve license file |
| protobuf | 7.36.2 | 3-Clause BSD License | metadata recorded |
| pyasn1 | 0.6.4 | BSD-2-Clause | metadata recorded |
| pyasn1_modules | 0.4.2 | BSD | metadata recorded |
| pycparser | 3.0 | UNSPECIFIED | resolve wheel notice |
| pydantic | 2.13.5 | UNSPECIFIED | resolve wheel notice |
| pydantic_core | 2.46.5 | UNSPECIFIED | resolve wheel notice |
| pyserial | 3.5 | BSD | metadata recorded |
| pywin32 | 312 | PSF | metadata recorded; preserve notice |
| pywinauto | 0.6.9 | BSD 3-clause | metadata recorded |
| referencing | 0.37.0 | UNSPECIFIED | resolve wheel notice |
| requests | 2.34.2 | Apache-2.0 | metadata recorded |
| rpds-py | 2026.6.3 | UNSPECIFIED | resolve wheel notice |
| six | 1.17.0 | MIT | metadata recorded |
| sniffio | 1.3.1 | MIT OR Apache-2.0 | metadata recorded; retain applicable notice |
| sounddevice | 0.5.6 | UNSPECIFIED | resolve wheel and PortAudio notices |
| srt | 3.5.3 | MIT | metadata recorded |
| tenacity | 9.1.4 | Apache 2.0 | metadata recorded |
| tokenizers | 0.23.2 | UNSPECIFIED | resolve wheel notice |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | metadata recorded; include both notices |
| typing-inspection | 0.4.4 | UNSPECIFIED | resolve wheel notice |
| typing_extensions | 4.16.0 | UNSPECIFIED | resolve wheel notice |
| urllib3 | 2.8.0 | UNSPECIFIED | resolve wheel notice |
| Vosk | 0.3.45 | UNKNOWN | resolve wheel notice; API license alone does not clear wheel/model |
| websockets | 16.1.1 and 17.1 | UNSPECIFIED | duplicate distribution metadata; resolve packaging collision and notices |

The external Laya venv was separately observed at `laya==0.3.29`,
`transformers==4.57.6`, `torch==2.14.1`, `tokenizers==0.22.2`,
`safetensors==0.8.0`, `numpy==2.5.3`, `networkx==3.7`, and `psutil==7.2.2`.
Several package metadata license fields are unspecified, and its complete
transitive notice set has not been collected. Laya remains shadow-only and its
venv is per-user provisioned, not embedded in the native installer.

This records the current binary's observed package set, not a reproducible
dependency lock. The WPF dependency, self-contained .NET framework files,
Python standard-library bundle, model files, GENIE brand assets, and NSIS
compiler/output need their own artifact-level notice collection.

The owner-supplied GENIE brand assets do not have an accompanying rights record
in this tree. Commercial redistribution remains contingent on confirmation of
those rights. See `THIRD_PARTY_NOTICES.md` for direct component references and
model-specific licensing notes. This is an engineering inventory, not legal
advice or a commercial distribution clearance.
