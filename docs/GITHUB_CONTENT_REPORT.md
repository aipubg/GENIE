# GitHub content report

The `main` branch contains the GENIE source project and the required model
artifacts tracked through Git LFS. The repository deliberately excludes local
machine state and generated build material.

Included:

- GENIE source, tests, documentation, scripts and UI source.
- Laya model weights under `data/models/laya/weights/` via Git LFS.
- Vosk model under `data/models/vosk-model-small-en-us-0.15/` via Git LFS.
- `.gitattributes` declaring the model LFS contract.

Excluded by `.gitignore`:

- `data/workspace/` and browser profiles: cookies, sessions, downloads and
  machine-specific browser state.
- `data/*.db`, provider-local configuration and vault/secrets files.
- `.build/`, `backend-dist/`, `dist/`, `bin/`, `obj/` and other regenerated
  build/runtime outputs.
- `artifacts/`, logs, caches, temporary media and test output.
- Local virtual environments, including the Laya provisioning environment.

The excluded items are reproducible or machine-private. The `C:` drive was
not scanned or uploaded; Git only receives files inside this repository on
the `E:` drive.
