# Third-party notices

GENIE includes or provisions the components below. Notices must accompany any
redistributable package. This inventory covers declared direct dependencies
and known model/runtime assets; it is not a substitute for a generated,
version-specific transitive SBOM.

| Component | Version / pin | License/provenance | Distribution note |
|---|---|---|---|
| CommunityToolkit.Mvvm | 8.2.2 | MIT, NuGet package | Preserve upstream notice when redistributing. |
| cactus-needle | 2.0.15 | Apache-2.0, Python package; NEDLE2 native runtime is separately provisioned | Preserve package notice and native runtime license. |
| Laya source | `1e28ac20c0896b1c37a744cd11f740eb98f8b178` | Apache-2.0, [upstream source](https://github.com/NandhaKishorM/laya) | Provisioned under `%LOCALAPPDATA%\GENIE\models`; shadow-only. |
| Laya checkpoint | `convaiinnovations/laya` revision `55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851` | Apache-2.0, [model card](https://huggingface.co/convaiinnovations/laya) | Model license is separate from source license; do not promote routing. |
| Faster-Whisper small | `Systran/faster-whisper-small`, pinned revision in provisioning manifest | MIT, [model repository](https://huggingface.co/Systran/faster-whisper-small) | Model files are per-user provisioned; verify pinned manifest. |
| faster-whisper | 1.2.1 observed in the current embedded runtime | MIT, [upstream license](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE) | Pin exact resolved package versions in each release SBOM. |
| Hugging Face Transformers | 4.57.6 in the Laya environment | Apache-2.0, [upstream source](https://github.com/huggingface/transformers) | Isolated Laya environment; include transitive notices if redistributed. |
| Laya isolated environment | `laya==0.3.29`, `transformers==4.57.6`, `torch==2.14.1`, `tokenizers==0.22.2`, `safetensors==0.8.0`, `numpy==2.5.3`, `networkx==3.7`, `psutil==7.2.2` | Observed from the external pinned Laya venv; several installed METADATA license fields are unspecified | Collect exact wheel notices before distributing the model environment. |
| OpenCV Python | 5.0.0.93 | Apache-2.0 for OpenCV; wheel may bundle third-party codecs | Review wheel's bundled notices for the release. |
| Pillow | 12.3.0 in current embedded runtime | MIT-CMU/PIL, [upstream license](https://github.com/python-pillow/Pillow/blob/main/LICENSE) | Preserve the package notice. |
| google-genai | 2.25.0 in current embedded runtime | Apache-2.0, [upstream license](https://github.com/googleapis/python-genai/blob/main/LICENSE) | Preserve the package notice. |
| Vosk API | 0.3.45 in current embedded runtime | Upstream API repository Apache-2.0, [upstream](https://github.com/alphacep/vosk-api); installed wheel metadata says `UNKNOWN` | Resolve exact wheel notice and model-specific terms in release SBOM. |
| Python 3.12.6 embeddable | 3.12.6 | PSF license | Include the Python license and bundled third-party notices. |
| .NET 8 runtime | Self-contained Windows publish | MIT/runtime notices from Microsoft | Preserve generated publish notices. |
| NSIS | 3.10 toolchain | zlib license | Build tool only; verify installer output licensing for each release. |

The product also contains owner-provided brand assets under `assets/`. Their
redistribution rights are not established by repository metadata; obtain and
record owner confirmation before external commercial distribution. The exact
resolved Python dependency graph and bundled native DLL notices remain release
gates; do not infer them from this direct-dependency summary.
