"""Generate the spike's synthetic fixtures deterministically.

Fifty images in ten categories, thirty notes on ten topics, and queries for
each with the category or topic they should retrieve. The images exist to give
the encoders varied input, so that rankings mean something when comparing
backends. They are not the evaluation set; that is plan item 2.

Run with: uv run --with pillow==12.3.0 python scripts/make_fixtures.py
"""

from __future__ import annotations

import json
import random
from itertools import pairwise
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "public" / "fixtures"
W, H = 448, 336
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
SEED = 20261007


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


def terminal(rng: random.Random, failing: bool) -> Image.Image:
    img = Image.new("RGB", (W, H), (18, 18, 24))
    d = ImageDraw.Draw(img)
    f = font(13)
    cmds = ["terraform apply", "kubectl get pods", "npm run build", "pytest -q", "make deploy"]
    y = 10
    for _ in range(rng.randint(8, 12)):
        d.text((10, y), "$ " + rng.choice(cmds), fill=(200, 200, 200), font=f)
        y += 18
        if failing and rng.random() < 0.5:
            msg = rng.choice(
                [
                    "Error: resource still in use",
                    "ERROR: connection refused",
                    "FAILED tests/test_api.py::test_upload",
                    "panic: index out of range",
                ]
            )
            d.text((10, y), msg, fill=(235, 70, 70), font=f)
        else:
            msg = rng.choice(["ok", "PASS 42 tests", "Apply complete!", "Running", "done in 3.2s"])
            d.text((10, y), msg, fill=(90, 210, 110), font=f)
        y += 18
        if y > H - 30:
            break
    return img


def axes(d: ImageDraw.ImageDraw) -> None:
    d.line([(40, 20), (40, H - 30), (W - 20, H - 30)], fill=(60, 60, 60), width=2)


def bar_chart(rng: random.Random) -> Image.Image:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    axes(d)
    n = rng.randint(4, 8)
    bw = (W - 80) // n
    color = rng.choice([(66, 133, 244), (52, 168, 83), (251, 188, 5), (234, 67, 53)])
    for i in range(n):
        h = rng.randint(30, H - 70)
        x = 50 + i * bw
        d.rectangle([x, H - 30 - h, x + bw - 10, H - 31], fill=color)
    return img


def line_chart(rng: random.Random) -> Image.Image:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    axes(d)
    for c in rng.sample([(66, 133, 244), (234, 67, 53), (52, 168, 83)], 2):
        pts, y = [], rng.randint(60, H - 60)
        for x in range(45, W - 20, 20):
            y = max(25, min(H - 35, y + rng.randint(-30, 30)))
            pts.append((x, y))
        d.line(pts, fill=c, width=3)
    return img


def red_circles(rng: random.Random) -> Image.Image:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    for _ in range(rng.randint(1, 5)):
        r = rng.randint(20, 70)
        x, y = rng.randint(r, W - r), rng.randint(r, H - r)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(220, rng.randint(0, 40), rng.randint(0, 40)))
    return img


def blue_squares(rng: random.Random) -> Image.Image:
    img = Image.new("RGB", (W, H), (240, 240, 235))
    d = ImageDraw.Draw(img)
    for _ in range(rng.randint(2, 6)):
        s = rng.randint(30, 90)
        x, y = rng.randint(0, W - s), rng.randint(0, H - s)
        d.rectangle([x, y, x + s, y + s], fill=(rng.randint(0, 40), rng.randint(40, 90), 200))
    return img


def diagram(rng: random.Random) -> Image.Image:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    f = font(12)
    labels = rng.sample(["API", "Queue", "Worker", "S3", "Database", "Cache", "Browser", "CDN"], 4)
    boxes = []
    for i, label in enumerate(labels):
        x = 30 + (i % 2) * 230
        y = 40 + (i // 2) * 150
        boxes.append((x, y))
        d.rectangle([x, y, x + 150, y + 60], outline=(40, 40, 40), width=2)
        d.text((x + 20, y + 22), label, fill=(20, 20, 20), font=f)
    for (x1, y1), (x2, y2) in pairwise(boxes):
        d.line([(x1 + 75, y1 + 60), (x2 + 75, y2)], fill=(40, 40, 40), width=2)
        d.polygon([(x2 + 75, y2), (x2 + 69, y2 - 10), (x2 + 81, y2 - 10)], fill=(40, 40, 40))
    return img


def table(rng: random.Random) -> Image.Image:
    img = Image.new("RGB", (W, H), (250, 250, 252))
    d = ImageDraw.Draw(img)
    f = font(11)
    d.rectangle([0, 0, W, 28], fill=(35, 47, 62))
    d.text((10, 8), "Instances (12)", fill="white", font=f)
    cols = ["Name", "State", "Type", "Zone"]
    for i, c in enumerate(cols):
        d.text((10 + i * 110, 38), c, fill=(20, 20, 20), font=f)
    for r in range(9):
        y = 60 + r * 28
        d.line([(0, y - 4), (W, y - 4)], fill=(220, 220, 225))
        row = [
            f"web-{rng.randint(1, 99)}",
            rng.choice(["running", "stopped"]),
            rng.choice(["t3.small", "m7i.large", "g5.xlarge"]),
            rng.choice(["eu-west-1a", "eu-west-1b"]),
        ]
        for i, v in enumerate(row):
            d.text((10 + i * 110, y), v, fill=(60, 60, 60), font=f)
    return img


def sunset(rng: random.Random) -> Image.Image:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    top = (rng.randint(60, 110), rng.randint(20, 60), rng.randint(110, 160))
    bottom = (250, rng.randint(120, 170), rng.randint(40, 80))
    horizon = rng.randint(200, 250)
    for y in range(horizon):
        t = y / horizon
        d.line(
            [(0, y), (W, y)],
            fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)),
        )
    d.rectangle([0, horizon, W, H], fill=(20, 30, 60))
    x, r = rng.randint(120, 330), rng.randint(30, 50)
    d.ellipse([x - r, horizon - r, x + r, horizon + r], fill=(255, 210, 90))
    d.rectangle([0, horizon, W, H], fill=(20, 30, 60))
    return img


def document(rng: random.Random) -> Image.Image:
    img = Image.new("RGB", (W, H), (235, 235, 235))
    d = ImageDraw.Draw(img)
    d.rectangle([60, 10, W - 60, H], fill="white")
    d.rectangle([80, 30, 80 + rng.randint(140, 240), 44], fill=(40, 40, 40))
    y = 62
    while y < H - 20:
        d.rectangle([80, y, W - 80 - rng.randint(0, 60), y + 5], fill=(170, 170, 170))
        y += rng.choice([12, 12, 12, 24])
    return img


CATEGORIES = {
    "terminal-error": lambda r: terminal(r, failing=True),
    "terminal-ok": lambda r: terminal(r, failing=False),
    "bar-chart": bar_chart,
    "line-chart": line_chart,
    "red-circles": red_circles,
    "blue-squares": blue_squares,
    "diagram": diagram,
    "table": table,
    "sunset": sunset,
    "document": document,
}

IMAGE_QUERIES = [
    ("a terminal showing an error message", "terminal-error"),
    ("command line output with a failure in red", "terminal-error"),
    ("terminal with successful command output", "terminal-ok"),
    ("green text in a dark console", "terminal-ok"),
    ("a bar chart", "bar-chart"),
    ("vertical bars on a graph", "bar-chart"),
    ("a line graph", "line-chart"),
    ("two lines plotted over time", "line-chart"),
    ("red circles on a white background", "red-circles"),
    ("red dots", "red-circles"),
    ("blue squares", "blue-squares"),
    ("dark blue rectangles", "blue-squares"),
    ("an architecture diagram with boxes and arrows", "diagram"),
    ("flowchart", "diagram"),
    ("a table listing cloud instances", "table"),
    ("a spreadsheet with rows of text", "table"),
    ("a sunset over the sea", "sunset"),
    ("orange evening sky", "sunset"),
    ("a page of text", "document"),
    ("a printed document", "document"),
]

TOPICS = {
    "spot": [
        "The spot node was reclaimed after a two minute interruption notice.",
        "Karpenter replaced the interrupted spot instance within ninety seconds.",
        "Drain pods before a spot interruption to avoid losing work.",
    ],
    "terraform": [
        "Terraform failed to delete the security group because an ENI still used it.",
        "terraform destroy hung on the VPC until the load balancer was removed.",
        "Import the bucket into state before running terraform apply again.",
    ],
    "lifecycle": [
        "S3 lifecycle rules moved old objects to Glacier after ninety days.",
        "Small objects cost more to transition than they save in storage.",
        "Abort incomplete multipart uploads after seven days.",
    ],
    "docker": [
        "The docker build cache was invalidated by copying the whole repository first.",
        "Use a multi-stage Dockerfile to keep the runtime image small.",
        "Pin the base image digest so rebuilds are reproducible.",
    ],
    "postgres": [
        "The query planner ignored the index until ANALYZE ran on the table.",
        "Add a partial index for rows where deleted_at is null.",
        "VACUUM reclaimed space after the bulk delete.",
    ],
    "dns": [
        "The DNS change took an hour because the old TTL was 3600 seconds.",
        "Lower the record TTL a day before migrating the domain.",
        "The CNAME at the zone apex was rejected; use an alias record.",
    ],
    "python": [
        "uv lock resolved the dependency conflict between numpy and torch.",
        "The wheel failed to build without the system compiler installed.",
        "Use a virtual environment per project to avoid package clashes.",
    ],
    "git": [
        "An interactive rebase squashed the fixup commits before merge.",
        "git bisect found the commit that broke the build in six steps.",
        "Force push with lease so you do not overwrite a colleague's work.",
    ],
    "tls": [
        "The certificate expired because the renewal cron job had stopped.",
        "Let's Encrypt rate limits blocked reissuing the wildcard certificate.",
        "The client rejected the chain because the intermediate was missing.",
    ],
    "cooking": [
        "Salt the pasta water generously before it boils.",
        "Rest the steak for five minutes after cooking.",
        "Toast the spices in a dry pan to bring out the flavour.",
    ],
}

NOTE_QUERIES = [
    ("spot instance interruption", "spot"),
    ("node reclaimed by AWS", "spot"),
    ("terraform destroy failed", "terraform"),
    ("infrastructure as code state problem", "terraform"),
    ("archive old objects to cheaper storage", "lifecycle"),
    ("multipart uploads cleanup", "lifecycle"),
    ("container image build is slow", "docker"),
    ("smaller container images", "docker"),
    ("database index not used", "postgres"),
    ("reclaim disk space in the database", "postgres"),
    ("domain name change slow to propagate", "dns"),
    ("apex record", "dns"),
    ("python dependency conflict", "python"),
    ("package installation failed", "python"),
    ("find the commit that broke things", "git"),
    ("clean up commit history", "git"),
    ("https certificate expired", "tls"),
    ("certificate chain error", "tls"),
    ("how to cook pasta", "cooking"),
    ("kitchen tips", "cooking"),
]


def main() -> None:
    (OUT / "images").mkdir(parents=True, exist_ok=True)
    images = []
    for cat, make in CATEGORIES.items():
        for i in range(5):
            rng = random.Random(f"{SEED}-{cat}-{i}")
            name = f"{cat}-{i}.png"
            make(rng).save(OUT / "images" / name, optimize=True)
            images.append({"file": f"images/{name}", "category": cat})
    notes = [
        {"id": f"{topic}-{i}", "topic": topic, "text": text}
        for topic, texts in TOPICS.items()
        for i, text in enumerate(texts)
    ]
    fixtures = {
        "seed": SEED,
        "images": images,
        "notes": notes,
        "image_queries": [{"text": q, "relevant": c} for q, c in IMAGE_QUERIES],
        "note_queries": [{"text": q, "relevant": t} for q, t in NOTE_QUERIES],
    }
    (OUT / "fixtures.json").write_text(json.dumps(fixtures, indent=2) + "\n")
    print(
        f"{len(images)} images, {len(notes)} notes, "
        f"{len(IMAGE_QUERIES)} + {len(NOTE_QUERIES)} queries -> {OUT}"
    )


if __name__ == "__main__":
    main()
