"""LocateAnything-3B inference worker — runs in its OWN process.

Lucy's core process mixes ctranslate2 (faster-whisper) and torch, whose
bundled cuDNN builds clash on Windows: the first vision inference resolved
the wrong cudnn DLL and hard-aborted the whole server (no Python traceback,
exit code 9). A fresh process gives torch a clean DLL search path, killing
the process guarantees the VRAM comes back, and a native crash here costs
one detection instead of the assistant.

Protocol: JSON lines. stdin  <- {"cmd": "detect", "image_b64": ..., "query": ...}
                              | {"cmd": "exit"}
          stdout -> {"event": "ready"} once the model is up, then one
                    {"ok": ..., ...} object per request, in order.

Library noise (progress bars, remote-code prints) is rerouted to stderr so
stdout stays pure protocol.
"""
import io, os, re, sys, json, time, base64
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

# Real stdout is reserved for the protocol; anything the libraries print()
# lands on stderr instead of corrupting the JSON stream.
_proto = sys.stdout
sys.stdout = sys.stderr


def emit(obj):
    _proto.write(json.dumps(obj, ensure_ascii=True) + "\n")
    _proto.flush()


# decord (video decoding) has no Windows/py3.13 wheel; the remote code only
# needs it importable when we send still frames.
import importlib.util
if importlib.util.find_spec("decord") is None:
    sys.path.insert(0, str(Path(__file__).parent / "_stubs"))

MODEL_ID = os.environ.get("VISION_MODEL_ID", "nvidia/LocateAnything-3B")
MAX_NEW_TOKENS = int(os.environ.get("VISION_MAX_NEW_TOKENS", "256"))

_DETECT_PROMPT = "Locate all the instances that matches the following description: {}."

# 4-coord alternative must come first: the 2-coord pattern is its prefix
_TOKEN_RE = re.compile(
    r"<ref>(?P<label>.*?)</ref>"
    r"|<box><(?P<x1>\d+)><(?P<y1>\d+)><(?P<x2>\d+)><(?P<y2>\d+)></box>"
    r"|<box><(?P<px>\d+)><(?P<py>\d+)></box>")


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    area = ((a[2] - a[0]) * (a[3] - a[1])
            + (b[2] - b[0]) * (b[3] - b[1]) - inter)
    return inter / area if area > 0 else 0.0


def parse(answer, query):
    objects, points = [], []
    label = None
    for m in _TOKEN_RE.finditer(answer or ""):
        if m.group("label") is not None:
            label = m.group("label").strip()
        elif m.group("x1") is not None:
            x1, y1, x2, y2 = (int(m.group(g)) / 1000
                              for g in ("x1", "y1", "x2", "y2"))
            if x2 <= x1 or y2 <= y1:
                continue
            obj = {"label": label or query, "box": [x1, y1, x2, y2]}
            # decode loops re-emit near-identical boxes — keep the first
            if not any(o["label"] == obj["label"]
                       and _iou(o["box"], obj["box"]) > 0.85
                       for o in objects):
                objects.append(obj)
        else:
            x, y = int(m.group("px")) / 1000, int(m.group("py")) / 1000
            if not any(p["label"] == (label or query)
                       and abs(p["x"] - x) < 0.02 and abs(p["y"] - y) < 0.02
                       for p in points):
                points.append({"label": label or query, "x": x, "y": y})
        if len(objects) >= 32:
            break
    return objects, points


class Worker:
    def __init__(self):
        import torch
        from transformers import (AutoModel, AutoProcessor, AutoTokenizer,
                                  BitsAndBytesConfig)
        print(f"worker: loading {MODEL_ID} (4-bit NF4)...", file=sys.stderr)
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, trust_remote_code=True)
        self.processor = AutoProcessor.from_pretrained(MODEL_ID, trust_remote_code=True)
        self.model = AutoModel.from_pretrained(
            MODEL_ID,
            trust_remote_code=True,
            torch_dtype=torch.bfloat16,
            quantization_config=BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.bfloat16,
            ),
            device_map={"": 0},
        ).eval()
        self._gen_extras = None

    def detect(self, image_b64, query):
        torch = self.torch
        from PIL import Image
        t0 = time.perf_counter()
        img = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")
        messages = [{"role": "user", "content": [
            {"type": "image", "image": img},
            {"type": "text", "text": _DETECT_PROMPT.format(query)},
        ]}]
        text = self.processor.py_apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True)
        images, videos = self.processor.process_vision_info(messages)
        inputs = self.processor(text=[text], images=images, videos=videos,
                                return_tensors="pt").to(self.model.device)

        base = dict(
            input_ids=inputs["input_ids"],
            attention_mask=inputs["attention_mask"],
            pixel_values=inputs["pixel_values"].to(torch.bfloat16),
            max_new_tokens=MAX_NEW_TOKENS,
            use_cache=True,
            # Greedy decoding loops forever on coordinate tokens (measured:
            # one box repeated until max_new_tokens). The model card's
            # sampling setup terminates properly; low temp keeps boxes
            # steady between frames.
            do_sample=True,
            temperature=0.3,
            top_p=0.9,
            repetition_penalty=1.1,
        )
        if inputs.get("image_grid_hws") is not None:
            base["image_grid_hws"] = inputs["image_grid_hws"]

        # The remote-code generate() takes extra kwargs (tokenizer,
        # generation_mode, verbose) whose exact set varies by revision, and
        # "hybrid" MTP decoding needs optional runtimes. Probe once from
        # most- to least-specific and remember what worked.
        candidates = ([self._gen_extras] if self._gen_extras is not None else [
            {"tokenizer": self.tokenizer, "generation_mode": "hybrid", "verbose": False},
            {"tokenizer": self.tokenizer, "verbose": False},
            {"tokenizer": self.tokenizer},
            {},
        ])
        last_err = None
        for extras in candidates:
            try:
                with torch.inference_mode():
                    out = self.model.generate(**base, **extras)
                self._gen_extras = extras
                break
            except Exception as e:
                last_err = e
        else:
            raise RuntimeError(f"generate failed: {last_err}") from last_err

        if isinstance(out, tuple):
            out = out[0]
        if isinstance(out, list):
            out = out[0] if out else ""
        if isinstance(out, torch.Tensor):
            out = self.tokenizer.decode(
                out[0, inputs["input_ids"].shape[1]:], skip_special_tokens=False)

        objects, points = parse(str(out), query)
        return {"ok": True, "state": "ready", "objects": objects,
                "points": points,
                "ms": round((time.perf_counter() - t0) * 1000)}


def main():
    worker = Worker()
    emit({"event": "ready"})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            emit({"ok": False, "error": "bad request json"})
            continue
        if req.get("cmd") == "exit":
            break
        if req.get("cmd") == "detect":
            try:
                emit(worker.detect(req.get("image_b64", ""),
                                   req.get("query") or "all objects"))
            except Exception as e:
                emit({"ok": False, "state": "error",
                      "error": f"{type(e).__name__}: {str(e)[:300]}"})
        else:
            emit({"ok": False, "error": f"unknown cmd {req.get('cmd')!r}"})


if __name__ == "__main__":
    main()
