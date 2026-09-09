"""Generate the demo media fixtures: one reference image, one voice clip.

    python tools/make_demo_media.py            # skips whatever already exists
    python tools/make_demo_media.py --force    # regenerate

Writes `fixtures/media/mira_ref_01.jpg` — the path `fixtures/seed.json` already
points `perf_mira_vance.reference_images` at — and
`fixtures/media/NF_1042_ADR_v03.wav`, the ADR asset the clearance demo checks.

**Why generated, not sourced.** `CLAUDE.md` Demo safety: the repository and the
video are public, so no real person's likeness or voice may appear anywhere in
the submission. Mira Vance does not exist, and neither does the voice on that
clip. Generating both from a prompt is what makes that claim true rather than
merely intended.

The image also has a job to do: `vision_web_detection` (WU-32) needs a reference
image to search *with*, and `ImageSweep` cannot be demonstrated without one.
The clip gives the clearance pipeline actual bytes rather than a filename.

Needs Vertex credentials — `gcloud auth application-default login
--project=consentinel` — and `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION`
from `.env`. Costs a few cents of hackathon credit per run, which is why it
skips existing files unless you pass `--force`.
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

MEDIA = Path(__file__).resolve().parent.parent / "fixtures" / "media"
IMAGE_OUT = MEDIA / "mira_ref_01.jpg"
AUDIO_OUT = MEDIA / "NF_1042_ADR_v03.wav"

IMAGE_MODELS = (
    ("global", "gemini-3-pro-image"),
    ("global", "gemini-3.1-flash-image"),
    (None, "gemini-2.5-flash-image"),        # None = the configured location
)
"""(location, model) pairs, tried in order.

**Imagen is not available on project `consentinel`** — every `imagen-*` id
returns 404 in both `us-central1` and `global`, and none appear in
`models.list()`. `CLAUDE.md` said the fixtures were made with Imagen 3; that has
been corrected to name what actually produced them. The requirement the rule
exists for is unchanged: the face is invented, not borrowed.

The Gemini 3 image models are `global`-only here, so the location travels with
the model rather than coming from `.env`."""

TTS_MODELS = ("gemini-2.5-flash-tts", "gemini-2.5-pro-tts")

IMAGE_PROMPT = (
    "Studio headshot of a completely fictional woman in her mid-thirties, "
    "shoulder-length dark hair, neutral grey backdrop, soft even key light, "
    "calm neutral expression, looking straight at the camera. Photographic, "
    "sharp focus, shallow depth of field. An ordinary actor's casting "
    "headshot. No text, no watermark, no logo, no other people."
)
"""Deliberately generic. A prompt naming any real person, or a style copied from
one, would defeat the point of generating the image at all."""

# One line of ADR for the fictional picture in fixtures/seed.json. Innocuous on
# purpose: this audio ends up in a public demo video.
AUDIO_SCRIPT = (
    "Say this calmly, like film dialogue recorded in a quiet booth: "
    "The lights over Carson never did go out. I just stopped looking at them."
)
VOICE = "Aoede"

SAMPLE_RATE = 24_000     # Gemini TTS returns 24 kHz, 16-bit, mono PCM
SAMPLE_WIDTH = 2
CHANNELS = 1


def _client(location: str | None = None):
    import os

    from dotenv import load_dotenv
    from google import genai

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    if location is None:
        return genai.Client()
    return genai.Client(vertexai=True, location=location,
                        project=os.environ["GOOGLE_CLOUD_PROJECT"])


def _as_jpeg(data: bytes) -> bytes:
    """The image models return PNG; `seed.json` points at a `.jpg`.

    Converting keeps that path honest — a PNG wearing a `.jpg` extension is the
    kind of thing that works until something reads the magic bytes.
    """
    import io

    from PIL import Image

    image = Image.open(io.BytesIO(data)).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92, optimize=True)
    return buffer.getvalue()


def make_image(force: bool = False) -> Path:
    """A generated portrait of a person who does not exist."""
    if IMAGE_OUT.exists() and not force:
        print(f"skip  {IMAGE_OUT.name} (exists)")
        return IMAGE_OUT

    from google.genai import types

    errors: list[str] = []
    for location, model in IMAGE_MODELS:
        try:
            # Bind the client to a name: a temporary one gets collected, and
            # its transport closes, before the response is read.
            client = _client(location)
            response = client.models.generate_content(
                model=model, contents=IMAGE_PROMPT,
                config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
            )
            raw = next(
                part.inline_data.data
                for part in response.candidates[0].content.parts
                if getattr(part, "inline_data", None)
            )
        except Exception as exc:  # noqa: BLE001 - try the next model, report at the end
            errors.append(f"{location or 'env'}/{model}: "
                          f"{type(exc).__name__}: {str(exc)[:160]}")
            continue

        data = _as_jpeg(raw)
        MEDIA.mkdir(parents=True, exist_ok=True)
        IMAGE_OUT.write_bytes(data)
        print(f"wrote {IMAGE_OUT}  ({len(data):,} bytes JPEG, "
              f"from {len(raw):,} bytes, {location or 'env'}/{model})")
        return IMAGE_OUT

    raise RuntimeError("no image model worked:\n  " + "\n  ".join(errors))


def make_voice_clip(force: bool = False) -> Path:
    """A synthetic voice reading one line of the fictional picture's dialogue."""
    if AUDIO_OUT.exists() and not force:
        print(f"skip  {AUDIO_OUT.name} (exists)")
        return AUDIO_OUT

    from google.genai import types

    client = _client()
    errors: list[str] = []
    for model in TTS_MODELS:
        try:
            response = client.models.generate_content(
                model=model, contents=AUDIO_SCRIPT,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=VOICE))),
                ),
            )
            pcm = next(
                part.inline_data.data
                for part in response.candidates[0].content.parts
                if getattr(part, "inline_data", None)
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{model}: {type(exc).__name__}: {str(exc)[:160]}")
            continue

        MEDIA.mkdir(parents=True, exist_ok=True)
        # Raw PCM out, WAV in: the clearance pipeline and any player want a
        # header, and 44 bytes of it is cheaper than another dependency.
        with wave.open(str(AUDIO_OUT), "wb") as wav:
            wav.setnchannels(CHANNELS)
            wav.setsampwidth(SAMPLE_WIDTH)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(pcm)
        seconds = len(pcm) / (SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS)
        print(f"wrote {AUDIO_OUT}  ({len(pcm):,} bytes, {seconds:.1f}s, {model})")
        return AUDIO_OUT

    raise RuntimeError("no TTS model worked:\n  " + "\n  ".join(errors))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true",
                    help="regenerate even if the file already exists")
    ap.add_argument("--only", choices=("image", "audio"),
                    help="generate just one of the two")
    args = ap.parse_args()

    failures: list[str] = []
    if args.only != "audio":
        try:
            make_image(args.force)
        except Exception as exc:  # noqa: BLE001 - report both, fail once
            failures.append(str(exc))
    if args.only != "image":
        try:
            make_voice_clip(args.force)
        except Exception as exc:  # noqa: BLE001
            failures.append(str(exc))

    for f in failures:
        print(f"FAILED {f}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
