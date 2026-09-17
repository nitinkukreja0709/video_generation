#!/usr/bin/env python3
"""videoforge server — web dashboard + job runner over the pipeline.

Run:  python3 server/app.py [--port 8790] [--host 0.0.0.0]

Endpoints:
  GET  /                     SPA dashboard
  GET  /api/stories          list story files
  GET  /api/story/<name>     story JSON
  PUT  /api/story/<name>     save story JSON (validated)
  GET  /api/boxes            liveness of configured ComfyUI boxes (& story box set)
  POST /api/run              start a pipeline job  {story, stages[], test_scene?}
  GET  /api/jobs             list known jobs (memory + persisted state files)
  GET  /api/jobs/<id>        live job state (progress, log, results)
  GET  /api/jobs/<id>/stop   request graceful stop
  GET  /media/<vid>/<path>   serve files under work/<vid>
"""
import argparse, json, os, re, threading, time, uuid, importlib.util
from flask import Flask, jsonify, request, send_from_directory, abort, Response

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORY_DIR = os.path.join(ROOT, "stories")
WORK = os.path.join(ROOT, "work")

# ---- import pipeline module (bin/videoforge, no .py extension) ----
_VF = os.path.join(ROOT, "bin", "videoforge")
_spec = importlib.util.spec_from_loader("videoforge", importlib.machinery.SourceFileLoader("videoforge", _VF))
vf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vf)

app = Flask(__name__, static_folder="static", static_url_path="/static")

_LOCK = threading.Lock()
_NEXT_JOB = {"pipeline": None, "stop": False}

def default_story(name):
    return {
        "meta": {"vid": name, "fps": 16, "clip_frames": 81, "voice": "hi-IN-MadhurNeural",
                 "voice_volume": 1.0, "music_volume": 0.32},
        "boxes": {},
        "character": {"physical": "", "identity": ""},
        "style": {"global_prompt": "", "negative_prompt": ""},
        "scenes": [],
    }

# ---- box liveness (async, from configured boxes: default = both boxes, overridden by story) ----
DEFAULT_BOXES = {
    "promax": {"name": "promax", "base": "http://100.81.202.86:8188"},
    "spark2": {"name": "spark2", "base": "http://100.108.126.6:8188"},
}

def _gen_uid():
    return uuid.uuid4().hex[:12]

def _check_box(base, name):
    h = vf.health_check(base, timeout=6)
    return {"name": name, "base": base, "status": h[0], "detail": h[1]}

def _boxes_from_story(story_name):
    p = os.path.join(STORY_DIR, story_name)
    if not os.path.exists(p):
        return DEFAULT_BOXES
    with open(p) as f:
        try:
            return json.load(f).get("boxes") or DEFAULT_BOXES
        except Exception:
            return DEFAULT_BOXES

# ---- persistence: job state files under work/<vid>/run_<jobid>.json ----
def _state_path(vid, jobid):
    return os.path.join(WORK, vid, f"run_{jobid}.json")

def _load_known_jobs():
    out = []
    if not os.path.isdir(WORK):
        return out
    for d in sorted(os.listdir(WORK)):
        for f in os.listdir(os.path.join(WORK, d)):
            if f.startswith("run_") and f.endswith(".json"):
                try:
                    with open(os.path.join(WORK, d, f)) as fh:
                        out.append(json.load(fh))
                except Exception:
                    continue
    out.sort(key=lambda j: j.get("finished") or j.get("started") or "", reverse=True)
    return out

def _save_state(state):
    try:
        with open(_state_path(state["vid"], state["job_id"]), "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass

# ---- job runner ----
def _validate_story(story_name, stages, test_scene):
    errs = []
    p = os.path.join(STORY_DIR, story_name)
    if not os.path.exists(p):
        errs.append(f"story does not exist: {story_name}")
        return errs
    with open(p) as f:
        try:
            cfg = json.load(f)
        except Exception as e:
            errs.append(f"invalid JSON: {e}")
            return errs
    if not cfg.get("meta", {}).get("vid"):
        errs.append("meta.vid missing")
    if not cfg.get("boxes"):
        errs.append("boxes missing (need at least one ComfyUI endpoint)")
    if not cfg.get("scenes"):
        errs.append("scenes missing")
    if test_scene:
        ids = {s.get("id") for s in cfg.get("scenes", [])}
        if test_scene not in ids:
            errs.append(f"test scene '{test_scene}' not in scenes")
    for st in stages:
        if st not in ("storyboard", "animate", "voice", "render"):
            errs.append(f"unknown stage: {st}")
    return errs

def _run_job(job, stages, test_scene):
    cfg_path = os.path.join(STORY_DIR, job["story"])
    with open(cfg_path) as f:
        cfg = json.load(f)
    vid = cfg["meta"]["vid"]
    vid_dir = os.path.join(WORK, vid)
    os.makedirs(os.path.join(vid_dir, "clips"), exist_ok=True)
    os.makedirs(os.path.join(vid_dir, "storyboard"), exist_ok=True)

    def progress(stage, scene, status, msg):
        with _LOCK:
            if _NEXT_JOB["stop"]:
                raise RuntimeError("stopped via UI")
            log = {"at": time.time(), "stage": stage, "scene": scene, "status": status, "msg": msg}
            job["log"].append(log)
            job["log"] = job["log"][-500:]
            if stage == "stage":
                job["stages"][scene] = {"status": status, "msg": msg}
            else:
                job["progress"].setdefault(stage, {})
                job["progress"][stage][scene] = {"status": status, "msg": msg}
            _save_state(job)

    with _LOCK:
        job["status"] = "running"
        job["started"] = time.time()
        job["stages"] = {}
        job["progress"] = {}
        job["log"] = []
        _save_state(job)

    try:
        if test_scene:
            # single-scene validation
            res = {"status": "error"}
            box = {"base": next(iter(cfg["boxes"].values()))["base"]}
            def _test_progress(stage, scene, status, msg):
                with _LOCK:
                    if _NEXT_JOB["stop"]:
                        raise RuntimeError("stopped via UI")
                    log = {"at": time.time(), "stage": stage, "scene": scene, "status": status, "msg": msg}
                    job["log"].append(log)
                    job["log"] = job["log"][-500:]
                    if stage == "stage":
                        job["stages"].setdefault(scene, {})
                        job["stages"][scene] = {"status": ("ok" if status in ("ok", "done", "skip") else ("error" if status == "error" else status)),
                                                "msg": msg or scene}
                    _save_state(job)
            if "storyboard" in stages:
                _test_progress("stage", "storyboard", "start", test_scene)
                vf.stage_storyboard_test(cfg, [box], vid_dir, test_scene)
                res = {"status": "test-run", "wd": vid_dir}
                _test_progress("stage", "storyboard", "ok", "generated test storyboard")
            elif "animate" in stages:
                _test_progress("stage", "animate", "start", test_scene)
                vf.stage_animate_test(cfg, [box], vid_dir, test_scene)
                res = {"status": "test-run", "wd": vid_dir}
                _test_progress("stage", "animate", "ok", "generated test clip")
            else:
                res = {"status": "error", "errors": [("stages", "pipeline test needs --test with storyboard or animate stage")]}
        else:
            res = vf.run_pipeline(cfg_path, stages=tuple(stages), progress=progress)
        with _LOCK:
            job["status"] = "done" if res.get("status") in ("done", "test-run") else "error"
            job["result"] = res
            job["finished"] = time.time()
            if res.get("final"):
                job["final_vid"] = res["final"].replace(WORK + os.sep, "").replace("\\", "/")
            _save_state(job)
    except Exception as e:
        with _LOCK:
            job["status"] = "error"
            job["finished"] = time.time()
            job["error"] = str(e)
            _save_state(job)

# ---- API ----
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

@app.route("/api/stories")
def api_stories():
    names = sorted(f for f in os.listdir(STORY_DIR) if f.endswith(".json"))
    items = []
    for n in names:
        p = os.path.join(STORY_DIR, n)
        try:
            with open(p) as fh:
                cfg = json.load(fh)
            scenes = len(cfg.get("scenes", []))
            vid = cfg.get("meta", {}).get("vid", "")
        except Exception:
            scenes, vid = 0, ""
        items.append({"name": n, "vid": vid, "scenes": scenes,
                      "bytes": os.path.getsize(p)})
    return jsonify(items)

@app.route("/api/story/<name>")
def api_story(name):
    if not re.fullmatch(r"[\w.-]+", name) or not name.endswith(".json"):
        abort(400)
    p = os.path.join(STORY_DIR, name)
    if not os.path.exists(p):
        abort(404)
    with open(p) as f:
        return Response(f.read(), mimetype="application/json")

@app.route("/api/story/<name>", methods=["PUT"])
def api_story_put(name):
    if not re.fullmatch(r"[\w.-]+", name) or not name.endswith(".json"):
        abort(400)
    p = os.path.join(STORY_DIR, name)
    try:
        cfg = request.get_json(force=True)
    except Exception as e:
        return jsonify({"error": f"invalid JSON body: {e}"}), 400
    if not isinstance(cfg, dict):
        return jsonify({"error": "body must be a JSON object"}), 400
    if "meta" in cfg and not cfg["meta"].get("vid"):
        cfg["meta"]["vid"] = name[:-5]
    # write temp then rename (atomic-ish)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)
    return jsonify({"ok": True, "name": name})

@app.route("/api/boxes")
def api_boxes():
    story = request.args.get("story")
    boxes = _boxes_from_story(story) if story else DEFAULT_BOXES
    t0 = time.time()
    threads = []
    results = {}
    def probe(k, b):
        try:
            results[k] = _check_box(b["base"], k)
        except Exception as e:
            results[k] = {"name": k, "base": b["base"], "status": "error", "detail": str(e)}
    for k, b in boxes.items():
        t = threading.Thread(target=probe, args=(k, b))
        t.start(); threads.append(t)
    for t in threads:
        t.join(timeout=8)
    return jsonify({"boxes": [results[k] for k in boxes.keys() if k in results],
                    "elapsed_ms": int((time.time() - t0) * 1000)})

@app.route("/api/run", methods=["POST"])
def api_run():
    data = request.get_json(force=True) or {}
    story = data.get("story", "")
    stages = data.get("stages") or ["storyboard", "animate", "voice", "render"]
    test_scene = data.get("test_scene")
    if isinstance(stages, str):
        stages = [s.strip() for s in stages.split(",") if s.strip()]
    if not re.fullmatch(r"[\w.-]+\.json", story):
        return jsonify({"error": "bad story name"}), 400
    errs = _validate_story(story, stages, test_scene)
    if errs:
        return jsonify({"error": errs}), 400
    with _LOCK:
        if _NEXT_JOB["pipeline"] and _NEXT_JOB["pipeline"]["status"] == "running":
            return jsonify({"error": "a job is already running — wait or stop it"}), 409
        job_id = _gen_uid()
        job = {"job_id": job_id, "story": story, "stages": stages, "test_scene": test_scene,
               "vid": data.get("vid") or story[:-5], "status": "queued", "started": None,
               "finished": None, "stages": {}, "progress": {}, "log": [], "result": None,
               "error": None}
        _NEXT_JOB["pipeline"] = job
        _NEXT_JOB["stop"] = False
        _save_state(job)
        t = threading.Thread(target=_run_job, args=(job, stages, test_scene), daemon=True)
        t.start()
    return jsonify({"ok": True, "job_id": job_id})

@app.route("/api/jobs")
def api_jobs():
    with _LOCK:
        live = (_NEXT_JOB["pipeline"],)
    known = _load_known_jobs()
    ids = {}
    merged = []
    for j in live:
        if j: merged.append(j); ids[j["job_id"]] = True
    for j in known:
        if j.get("job_id") not in ids:
            merged.append(j)
    merged.sort(key=lambda j: j.get("started") or time.time(), reverse=True)
    return jsonify(merged[:40])

@app.route("/api/jobs/<job_id>")
def api_job(job_id):
    with _LOCK:
        if _NEXT_JOB["pipeline"] and _NEXT_JOB["pipeline"]["job_id"] == job_id:
            j = _NEXT_JOB["pipeline"]
        else:
            j = None
    if j is None:
        p = None
        for root, _, files in os.walk(WORK):
            for f in files:
                if f == f"run_{job_id}.json":
                    p = os.path.join(root, f)
        if not p:
            abort(404)
        with open(p) as fh:
            j = json.load(fh)
    return jsonify(j)

@app.route("/api/jobs/<job_id>/stop", methods=["POST"])
def api_job_stop(job_id):
    with _LOCK:
        if _NEXT_JOB["pipeline"] and _NEXT_JOB["pipeline"]["job_id"] == job_id:
            _NEXT_JOB["stop"] = True
            return jsonify({"ok": True, "note": "stop requested (graceful)"})
    return jsonify({"error": "not the running job"}), 404

@app.route("/media/<vid>/<path:subpath>")
def media(vid, subpath):
    if not re.fullmatch(r"[\w.-]+", vid):
        abort(400)
    full = os.path.abspath(os.path.join(WORK, vid, subpath))
    if not full.startswith(os.path.abspath(WORK) + os.sep):
        abort(403)
    if not os.path.exists(full):
        abort(404)
    return send_from_directory(os.path.join(WORK, vid), subpath)

@app.route("/api/medias/<vid>")
def api_medias(vid):
    if not re.fullmatch(r"[\w.-]+", vid):
        abort(400)
    base = os.path.abspath(os.path.join(WORK, vid))
    if not base.startswith(os.path.abspath(WORK) + os.sep):
        abort(403)
    if not os.path.isdir(base):
        return jsonify({"storyboard": [], "clips": [], "voice": [], "final": None})
    def listing(sub):
        d = os.path.join(base, sub)
        if not os.path.isdir(d):
            return []
        return sorted(f for f in os.listdir(d)
                      if os.path.isfile(os.path.join(d, f)) and not f.startswith("."))
    final = "final.mp4" if os.path.exists(os.path.join(base, "final.mp4")) else None
    for f in os.listdir(base):
        if f.endswith(".mp4") and not f.startswith("run_"):
            final = final or f
    return jsonify({
        "storyboard": listing("storyboard"),
        "clips": listing("clips"),
        "voice": listing("voice"),
        "final": final,
    })

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    print(f"videoforge UI -> http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)

if __name__ == "__main__":
    main()