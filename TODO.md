# Remaining Work

## Rendering / Quality
- [ ] **Sharper render**: storyboards regenerate at higher res and/or Wan clips at 1216×672 natively instead of 832×480 for more native detail (currently 720p is a lanczos upscale — good but not native).
- [ ] Video transition tuning: current per-scene crossfades are uniform (0.4s). Add story-level `transition` config (fade/slide/cut) and optional title card / outro card.
- [ ] Burning subtitles: `meta.subtitles: true` exists in the story but is not implemented in `stage_render`. Add ASS subtitles from `narration` text using Devanagari-capable font (e.g. `Lohit-Devanagari` on laptop).
- [ ] Cinematic: optional vignette + film grain preset flags in render stage.

## Voice / Audio
- [ ] Music alternates with mood: current single synthesized tanpura drone for all stories. Add `meta.music: "drone" | "none"` and story-specific music source.
- [ ] Narration alignment: delays assume uniform clip length and scene start times; verify against `-t` and fade timings for non-uniform durations.
- [ ] Voice consistency across re-renders (Edge-TTS is deterministic per voice; keep voice id in story meta — done).

## Pipeline robustness
- [ ] **Spark2 ffmpeg is non-persistent**: `imageio-ffmpeg` (VHS_VideoCombine dep) is pip-installed into the running container's writable layer; lost on container recreate. Bake into the comfyui image or add a reinstall step (`pip install imageio-ffmpeg && docker restart`).
- [ ] Parallelize reliably: current submit-all-then-poll fills both boxes, but failures aren't retried. Add auto-retry-on-failure per clip and per-box health check before submit.
- [ ] Capacity/cooldowns: HuggingFace/PyPI throttled; model downloads must run only on the DGX boxes (never laptop) via ModelScope/dl.google.com routes.
- [ ] Test coverage: add a `--self-test` mode that validates a story JSON and pre-flights model/endpoint availability without generating.

## Stories / Content
- [ ] Second story as a regression test (e.g., nature scenes, no characters) to prove "any video".
- [ ] Character continuity between storyboards is prompt-driven only; evaluate whether regen-with-same-seed per scene improves it, or a reference-image + LoRA approach is needed.
- [ ] Auto-generate story JSON from a prompt (title, scenes, narration via LLM) — currently hand-authored.