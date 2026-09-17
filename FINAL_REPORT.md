# Final Report — Video Generation Pipeline

**Date:** 17 Sep 2026
**Repo:** https://github.com/nitinkukreja0709/video_generation

---

## 1. Objective

Build a reusable pipeline that generates **consistent, natural-voice narrated story videos** from a single story JSON — tunable for "any video". Two pain points drove it: the first Kabir video had AI-sounding narration (gTTS) and inconsistent visuals (text→video without anchoring). The final pipeline replaces both: Edge-TTS neural narration + SDXL storyboards anchoring every Wan-2.2 clip.

## 2. Infrastructure

Two NVIDIA **DGX Spark (GB10)** boxes on Tailscale, byte-identical deployments:

| | Promax | Spark2 |
|---|---|---|
| Tailscale IP | 100.81.202.86 | 100.108.126.6 |
| LAN IP | 192.168.1.23 | 192.168.1.56 |
| GPU | GB10 | GB10 |
| ComfyUI | 0.36.0 (:8188) | 0.36.0 (:8188) |

**Access notes:** laptop SSH works via LAN to Promax (`nvsync.key`); Spark2 SSH is intercepted by Tailscale browser-auth, so manage it over HTTP APIs or Tailscale.

**Hosting constraint:** model downloads run **only on the boxes** (never the laptop). Routes that work: ModelScope (~1.6 MB/s) and `dl.google.com` (~6 MB/s from Promax); HuggingFace/PyPI are throttled everywhere (~1–4 KB/s).

## 3. Models deployed

| Role | File | Box(es) |
|---|---|---|
| SDXL checkpoint | `sd_xl_base_1.0.safetensors` (6.94 GB, via ModelScope) | Promax `/home/admin/models/program/`, symlinked into ComfyUI `checkpoints/` |
| Wan i2v | `wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors` | both |
| Wan t2v | `wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors` (+ LoRA, high+low variants) | both |
| Wan LoRAs | `wan2.2_i2v/t2v_lightx2v_4steps_lora_v1.*` | both |
| CLIP | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` (type `wan`) | both |
| VAE | `wan_2.1_vae.safetensors` | both |

**Key fixes during setup:**
- Checkpoint symlinks live outside `models/` (`/home/admin/models/program`), so ComfyUI containers had to be restarted with that host dir mounted read-only.
- Spark2's container lacked `ffmpeg` → VHS failures fixed inside the container (`imageio-ffmpeg`); **note: this lives in the writable layer and is lost on container recreate** (open TODO).
- Uploaded images live in the container's writable layer — regenerated images must be re-uploaded after a restart.

## 4. Pipeline (`bin/videoforge`)

Four independent, **idempotent** stages (completed files are skipped, safe to re-run/resume):

```
story.json
  │
  ├─ storyboard   SDXL text→image per scene (1216×672)          [ComfyUI]
  ├─ animate      Wan-2.2 i2v from each storyboard              [ComfyUI]
  │                  (832×480 @16fps, 81 frames, 4-step lightx2v)
  │                  submitted round-robin across both boxes, polled in
  │                  parallel ThreadPool
  ├─ voice        Edge-TTS neural narration per scene            [laptop]
  └─ render       ffmpeg: crossfade chain + delayed narration +  [laptop]
                  tanpura music bed + 720p lanczos + unsharp
                  → final.mp4
```

CLI: `python3 -u bin/videoforge stories/<story>.json [--stages storyboard|
animate|voice|render] [--test <scene>]`. Outputs to `work/<vid>/`
(`final.mp4`, `storyboard/`, `clips/`, `voice/`, `music.wav`).

**Bugs found & fixed while building:**
- `start_image` must go through a `LoadImage` node; a bare filename string breaks (`'str' object has no attribute 'movedim'`).
- `VHS_VideoCombine` requires `pingpong: False`.
- Workflow POST payload needs the `{"prompt":{...},"client_id":...}` wrapper.
- Render audio stream indices must be `[vid_count+idx:a]`.
- **The big one:** chained xfade offsets were `i*clip_len` but must be `i*(clip_len-fade)` (overlap is cumulative). Wrong offsets pushed later scenes past the end of the stream → **black video after ~10 s even though clips were fine**. Also narration delays were re-aligned to scene-visible times, and render is now **720p via lanczos upscale + unsharp** for sharpness.

## 5. Deliverables

- **`kabir_doha/kabir_doha_final.mp4`** — 56.4 s, **1280×720 @16fps**, H.264+AAC, mean −16.6 dB / max −0.5 dB. Hindi narration (Madhur, Edge-TTS) + tanpura bed + crossfades. Verified: no black frames across the whole timeline (mean luma 116–190 throughout).
- 12 individual scene clips (Kabir story) with per-scene storyboards.
- First-iteration assets kept alongside for reference (`kabir_01..12.mp4`, `kabir_doha_1min.mp4` gTTS version, 9-clip preview).
- Git repo with README, TODO, story config, pipeline script pushed to GitHub.

## 6. Remaining work (see TODO.md for full list)

1. **Sharpness:** generate Wan clips at 1216×672 natively instead of upscaling 832×480.
2. **Subtitles:** `meta.subtitles` flag exists but not implemented — add ASS burn with a Devanagari font.
3. **Spark2 persistence:** bake `imageio-ffmpeg` into the ComfyUI image (writable-layer install is fragile).
4. **Robustness:** per-clip retry on box failure; pre-flight health check.
5. **Content:** a second story to prove "any video"; optional LLM auto-generation of story JSON.
6. **Audio:** per-story music switch (drone/none), narration-delay verification for non-uniform durations.

## 7. How to run

```bash
cd ~/videoforge
git pull                      # or see GitHub
python3 -u bin/videoforge stories/kabir_datepalm.json        # full run
python3 -u bin/videoforge stories/kabir_datepalm.json --stages render   # just re-render
python3 -u bin/videoforge stories/kabir_datepalm.json --test s06 --stages storyboard animate
```