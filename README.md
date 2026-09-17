# video_generation

Production pipeline for generating AI narrated story videos: **SDXL storyboards → Wan-2.2 video clips → Edge-TTS narration → music bed → ffmpeg render**.

Designed to produce consistent, natural-voice videos. Currently validated end-to-end with the "Kabir's Date Palm" story (12 scenes, Hindi).

## Architecture

```
story.json
  │
  ├─ storyboard   SDXL text→image per scene (1216×672)     [ComfyUI]
  │    │             consistent style/character/seed
  │    ▼
  ├─ animate      Wan-2.2 i2v: each clip animated from     [ComfyUI]
  │    │             its storyboard (832×480 @16fps, 81 f)
  │    ▼
  ├─ voice        Edge-TTS neural narration per scene        [laptop]
  │    ▼
  └─ render       ffmpeg: xfade crossfades + narration +    [laptop]
                     music + 720p lanczos upscale + unsharp
                     → final.mp4
```

Every stage is **idempotent** — completed files are skipped, so you can re-run or resume any stage. `animate` probes box health first and skips dead boxes, retries failed prompts up to 3×, and falls back so a single dead box can't stall the run.

## Setup

Models live on two ComfyUI boxes (Promax, Spark2) behind Tailscale:

| Role | Model | Location |
|------|-------|----------|
| checkpoint | `sd_xl_base_1.0.safetensors` | `/home/admin/models/program/` (symlink into `models/checkpoints/`) |
| i2v | `wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors` | `/home/admin/models/video/wan2.2/` |
| t2v | `wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors` | `/home/admin/models/video/wan2.2/` |
| LoRA | `wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors` (+ t2v variants) | same |
| CLIP | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` (type `wan`) | same |
| VAE | `wan_2.1_vae.safetensors` | same |

ComfyUI containers must mount the host model dirs read-only (`-v /home/admin/models/program:/home/admin/models/program:ro`), because checkpoint symlinks point at host paths.

## Usage

### Web UI (recommended)

```bash
cd videoforge
python3 server/app.py --port 8790          # http://<laptop>:8790
```

Dashboard lets you:

- write/edit a story JSON (with `new` + `save`), and pick a test scene
- check ComfyUI box liveness (promax / spark2 health badges)
- run the full pipeline or a subset of stages, one job at a time, with **stop**
- watch live per-stage progress + timestamped log
- preview storyboards, per-scene clips, and the finished video

Jobs run in a background thread; state is persisted to `work/<vid>/run_<job>.json`
so status survives a server restart. `POST /api/run` with
`{"story": "...json", "stages": [...], "test_scene": "s01"}` is the programmatic entry point.

### CLI

```bash
python3 -u bin/videoforge stories/<story>.json                 # run all stages
python3 -u bin/videoforge stories/<story>.json --stages storyboard   # subset
python3 -u bin/videoforge stories/<story>.json --test s06 --stages storyboard animate  # single scene
```

Output lands in `work/<vid>/final.mp4` (plus `storyboard/`, `clips/`, `voice/`, `music.wav`).

## Story format

`stories/*.json`:

- `meta` — title, language, Edge-TTS voice, fps, frames-per-clip, volumes.
- `style` — global prompt + negative prompt shared by all scenes.
- `character` — recurring character descriptor + identity-lock instruction for continuity.
- `boxes` — ComfyUI endpoints (round-robins clips across boxes).
- `scenes[]` — per scene: `id`, `cam`, `visual`, `narration`.