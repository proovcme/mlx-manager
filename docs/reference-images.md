# Reference images: backend investigation

Status: investigated; the manager does not yet accept reference images.

MFLUX's current source includes `mflux-generate-qwen-2.1-edit`, with instruction-based editing using one or several reference images. It conditions both the vision encoder and diffusion transformer on the actual images. A text description alone cannot provide that conditioning.

Sources:

- [Reference editing API and limitations](https://github.com/mflux-community/mflux/blob/main/src/mflux/models/qwen21/reference/README.md)
- [Editing adapter](https://github.com/mflux-community/mflux/blob/main/src/mflux/models/qwen21/variants/edit/qwen_image_21_edit.py)

The pinned MFLUX 0.20 backend used by this package has no editing variant. The current editing source imports successfully in an isolated checkout using the installed environment, but inference has not been validated. Updating the running text-to-image backend would also change component implementations and invalidate its existing benchmark baseline.

The inspected `mlx-community/Qwen-Image-2.1-MLX-4bit` snapshot includes 531 vision-related tensors and processor files. Its language and vision tensor names differ from the new editing loader's expected names. Those tensors need a separately tested mapping; their presence alone does not establish compatibility or quality. No extra weights have been downloaded.

Proposed manager behavior:

- Upload references with previews and remove controls. Preserve their order.
- Keep references attached to the image draft and pass the actual files to the editing worker.
- Use the same references for a series; save reference provenance in each result's private metadata.
- The prompt helper receives the edit instruction and reference context and preserves requested identities and unchanged details.
- Report encoding, denoising, decoding, errors and cancellation through the existing progress API.

Before enabling: validate one-reference and two-reference edits on the local Q4 checkpoint, memory usage on a 24 GB machine, staged encoder release, cancellation and checkpoint mappings. Reference fidelity is a model capability, not a guarantee of exact identity preservation.
