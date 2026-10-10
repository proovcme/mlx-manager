# From an idea to an image series

![Prompt expansion and series](../../docs/screenshots/prompt-series.jpg)

Choose Qwen Image, open **⋯ → Настройки изображения**, and enable
**Улучшать промпт перед генерацией** (expand before generating).
Select an installed text model; a small Qwen3-4B is a practical starting point.
**Улучшить промпт** previews a rewrite without rendering. You can edit the resulting
prompt or restore your original idea. The automatic mode rewrites once, unloads the
text model, then generates images sequentially. No weights are downloaded.

Set **В серии** to 1–20. Each image uses the same prompt and settings, with seeds
`seed`, `seed + 1`, and so on (wrapping at 32 bits). The UI shows the image number
and its generation steps. Stop cancels the current image and all remaining images;
completed images stay in the gallery. Refreshing the page retains control. Restarting
the service interrupts the pending queue; it does not automatically launch more images.

The compact local instructions follow Qwen's
[documented prompt rewrite approach](https://github.com/QwenLM/Qwen-Image-2.1/tree/main/prompt_rewrite):
a description of the finished frame, composition, materials and lighting; requested
lettering keeps its original script. This uses your chosen general text model,
not the dedicated PE checkpoint. A rewrite adds creative details and can alter the
result; review it when exact fidelity matters. Inference settings remain under your control.
Original and expanded prompts stay in local history. Failed or truncated rewrites
stop the workflow instead of being passed to the image model.

[Exact acceleration measurements](../../docs/acceleration.md) · [Reference-image investigation](../../docs/reference-images.md)


## Prompts and gallery

A static prompt guide opens separately from generation settings. Long prompts
are checked against the encoder's real token limit and rejected explicitly
rather than silently truncated.

Gallery items can be moved individually or together to Manager's recoverable
local trash. PNGs and saved parameters move together; chats remain intact.
Old images without recorded prompts cannot restore generation parameters.
