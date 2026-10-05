"""A native window that shows the running service's rotation as a Pixoo-64 LED panel.

python -m tools.pixoo_window --url http://192.168.1.171:8080 [--fixture <combo> | --date YYYY-MM-DD]
                             [--size 512] [--brightness 100] [--raw]

Reads `/pixoo/rotation.json` and the frame PNGs from the service on the LAN (stdlib tkinter
plus Pillow; no new dependencies) and draws the same LED look the /pixoo page draws: a round
LED per pixel, 70 % of the cell, over faint unlit LEDs, with a soft glow. Rendering stays on
the service; this window only maps pixels to LEDs. It starts at Today and runs the service's
sequence (Today, Week, Month, Books, the earned small wins, the party when the week is done).
Keys: space pause, n next screen, s sparkle, p party, q quit. Nothing is sent anywhere but the
service address given.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from io import BytesIO

import httpx
from PIL import Image, ImageChops, ImageDraw

from app.render.frame import SIZE

DOT = 0.70
GLOW = 0.55
UNLIT = (30, 30, 33)
SUPERSAMPLE = 4


def led_mask(size: int) -> Image.Image:
    """An 'L' image with one anti-aliased disc per cell, DOT of the cell wide."""
    big = size * SUPERSAMPLE
    cell = big / SIZE
    radius = cell * DOT / 2
    mask = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(mask)
    for y in range(SIZE):
        for x in range(SIZE):
            cx, cy = (x + 0.5) * cell, (y + 0.5) * cell
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=255)
    return mask.resize((size, size), Image.Resampling.LANCZOS)


def led_image(frame: Image.Image, size: int, lut: list[int], mask: Image.Image) -> Image.Image:
    """The panel look for one 64x64 frame: unlit dots, additive glow, lit discs on top."""
    mapped = frame.convert("RGB").point(lut * 3)
    mask_rgb = Image.merge("RGB", (mask, mask, mask))
    unlit = ImageChops.multiply(Image.new("RGB", (size, size), UNLIT), mask_rgb)
    glow = mapped.resize((size, size), Image.Resampling.BILINEAR)
    glow = ImageChops.multiply(glow, Image.new("RGB", (size, size), (round(255 * GLOW),) * 3))
    base = ImageChops.add(unlit, glow)
    discs = mapped.resize((size, size), Image.Resampling.NEAREST)
    lit = ImageChops.lighter(ImageChops.lighter(*mapped.split()[:2]), mapped.split()[2])
    lit = lit.point(lambda v: 255 if v else 0).resize((size, size), Image.Resampling.NEAREST)
    return Image.composite(discs, base, ImageChops.multiply(mask, lit))


@dataclass
class Rotation:
    data: dict
    frames: dict[str, list[Image.Image]] = field(default_factory=dict)

    @property
    def screens(self) -> list[dict]:
        return self.data["screens"]

    @property
    def celebrations(self) -> list[dict]:
        return self.data["celebrations"]


def fetch_rotation(client: httpx.Client, query: dict[str, str]) -> Rotation:
    rotation = Rotation(client.get("/pixoo/rotation.json", params=query).raise_for_status().json())
    for clip in rotation.screens + rotation.celebrations:
        rotation.frames[clip["name"]] = [
            Image.open(BytesIO(client.get(f["url"]).raise_for_status().content)).convert("RGB")
            for f in clip["frames"]
        ]
    return rotation


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="the service address")
    parser.add_argument("--fixture")
    parser.add_argument("--date")
    parser.add_argument("--size", type=int, default=512)
    parser.add_argument("--brightness", type=int, default=100, choices=range(10, 101, 10))
    parser.add_argument("--raw", action="store_true", help="skip the LED gamma emulator")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        import tkinter as tk

        from PIL import ImageTk
    except ImportError as exc:
        print(f"tkinter is not available in this Python: {exc}", file=sys.stderr)
        return 2
    query = {}
    if args.fixture:
        query["fixture"] = args.fixture
    elif args.date:
        query["date"] = args.date
    with httpx.Client(base_url=args.url, timeout=10.0, trust_env=False) as client:
        rotation = fetch_rotation(client, query)
    table = rotation.data["luts"]["raw" if args.raw else "gamma"][str(args.brightness)]
    mask = led_mask(args.size)
    bezel = round(args.size * 0.075)

    root = tk.Tk()
    root.title("life-dashboard device")
    root.configure(bg="#141210")
    panel = tk.Label(root, bg="#121214", bd=0, padx=bezel, pady=bezel)
    panel.pack(padx=24, pady=(24, 0))
    caption = tk.Label(root, bg="#141210", fg="#a8a39b", font=("Menlo", 11))
    caption.pack(pady=(10, 20))

    state = {
        "screen": 0,
        "frame": 0,
        "elapsed": 0,
        "step_ms": 0,
        "paused": False,
        "overlay": None,
        "job": None,
    }
    cache: dict[tuple[str, int], ImageTk.PhotoImage] = {}

    def show(name: str, index: int) -> None:
        key = (name, index)
        if key not in cache:
            image = led_image(rotation.frames[name][index], args.size, table, mask)
            cache[key] = ImageTk.PhotoImage(image)
        panel.configure(image=cache[key])

    def current() -> dict:
        return rotation.screens[state["screen"]]

    def describe() -> str:
        as_of = rotation.data["as_of_local"] or "no push yet"
        overlay = state["overlay"]
        if overlay:
            clip, index = overlay["clip"], overlay["frame"]
            return f"{clip['name'].upper()} · frame {index + 1}/{len(clip['frames'])} · {as_of}"
        screen = current()
        frame = screen["frames"][state["frame"]]
        held = f"{state['elapsed'] / 1000:.1f} of {screen['hold_ms'] / 1000:.0f} s"
        paused = " · paused" if state["paused"] else ""
        return (
            f"{screen['name'].upper()} · frame {state['frame'] + 1}/{len(screen['frames'])} · "
            f"{frame['ms']} ms · {held}{paused}\n{rotation.data['day_local']} · data as of {as_of}"
        )

    def schedule(ms: int) -> None:
        if state["job"] is not None:
            root.after_cancel(state["job"])
        state["job"] = root.after(ms, step)

    def next_screen() -> None:
        state["screen"] = (state["screen"] + 1) % len(rotation.screens)
        state["frame"] = state["elapsed"] = 0

    def step() -> None:
        state["job"] = None
        overlay = state["overlay"]
        if overlay:
            overlay["frame"] += 1
            if overlay["frame"] >= len(overlay["clip"]["frames"]):
                state["overlay"] = None
            else:
                show(overlay["clip"]["name"], overlay["frame"])
                caption.configure(text=describe())
                schedule(overlay["clip"]["frames"][overlay["frame"]]["ms"])
                return
        elif not state["paused"]:
            screen = current()
            state["elapsed"] += state["step_ms"]
            state["frame"] = (state["frame"] + 1) % len(screen["frames"])
            if state["elapsed"] >= screen["hold_ms"]:
                next_screen()
        screen = current()
        show(screen["name"], state["frame"])
        caption.configure(text=describe())
        left = max(1, screen["hold_ms"] - state["elapsed"])
        state["step_ms"] = min(screen["frames"][state["frame"]]["ms"], left)
        schedule(state["step_ms"] if not state["paused"] else 250)

    def play(name: str) -> None:
        clip = next(c for c in rotation.celebrations if c["name"] == name)
        state["overlay"] = {"clip": clip, "frame": 0}
        show(name, 0)
        caption.configure(text=describe())
        schedule(clip["frames"][0]["ms"])

    def on_key(event: tk.Event) -> None:
        key = event.keysym.lower()
        if key == "space":
            state["paused"] = not state["paused"]
            caption.configure(text=describe())
        elif key == "n":
            state["overlay"] = None
            next_screen()
            step()
        elif key == "s":
            play("sparkle")
        elif key == "p":
            play("party")
        elif key == "q":
            root.destroy()

    root.bind("<Key>", on_key)
    show(current()["name"], 0)
    caption.configure(text=describe())
    state["step_ms"] = min(current()["frames"][0]["ms"], current()["hold_ms"])
    schedule(state["step_ms"])
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
