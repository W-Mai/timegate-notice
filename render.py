import io
import unicodedata
from functools import lru_cache

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageSequence


SIZE = 128
PADDING = 5
TIME_STRIP = 22
TIME_TOP = SIZE - TIME_STRIP
MAX_TEXT = 48
MAX_STICKER_BYTES = 4 * 1024 * 1024
MAX_STICKER_DURATION = 8000
MAX_DECODED_PIXELS = 64_000_000
STICKER_FORMATS = ("GIF", "PNG", "JPEG", "WEBP")
BACKGROUND = (3, 8, 13)
FOREGROUND = (244, 252, 255)
PANELS = {1: (2,), 2: (1, 3), 3: (1, 2, 3), 4: (0, 1, 3, 4), 5: (0, 1, 2, 3, 4)}


class TextError(ValueError):
    pass


class StickerError(ValueError):
    pass


def normalize_text(value):
    if not isinstance(value, str):
        raise TextError("请输入文字")
    text = " ".join(unicodedata.normalize("NFC", value).split())
    if len(text) > MAX_TEXT:
        raise TextError(f"最多输入 {MAX_TEXT} 个字符")
    return text


def sticker_gif(data):
    frames = []
    offsets = []
    elapsed = 0
    try:
        with Image.open(io.BytesIO(data), formats=STICKER_FORMATS) as source:
            pixels = source.width * source.height
            if pixels > 16_000_000 or max(source.size) > 4096:
                raise StickerError("表情包尺寸太大")
            default_frame = bool(source.info.get("default_image"))
            for index, frame in enumerate(ImageSequence.Iterator(source)):
                if (index + 1) * pixels > MAX_DECODED_PIXELS or elapsed >= MAX_STICKER_DURATION:
                    break
                if index == 0 and default_frame:
                    continue
                frame.load()
                duration = frame.info.get("duration") or 100
                delay = max(20, min(MAX_STICKER_DURATION, round(duration / 10) * 10))
                if not offsets or elapsed - offsets[-1] >= 80:
                    fitted = ImageOps.contain(ImageOps.exif_transpose(frame).convert("RGBA"), (SIZE, SIZE))
                    tile = Image.new("RGB", (SIZE, SIZE), BACKGROUND)
                    tile.paste(fitted, ((SIZE - fitted.width) // 2, (SIZE - fitted.height) // 2), fitted)
                    frames.append(tile.quantize(colors=96))
                    offsets.append(elapsed)
                elapsed += delay
    except (Image.DecompressionBombError, OSError, SyntaxError, OverflowError) as error:
        raise StickerError("无法读取表情包，请选择 GIF、PNG、JPEG 或 WebP") from error
    if not frames:
        raise StickerError("表情包没有可用画面")
    durations = [end - start for start, end in zip(offsets, [*offsets[1:], min(elapsed, MAX_STICKER_DURATION)])]
    while True:
        output = io.BytesIO()
        frames[0].save(output, "GIF", save_all=True, append_images=frames[1:], duration=durations, loop=0)
        if output.tell() <= 900 * 1024:
            return output.getvalue()
        frames = frames[::2]
        durations = [sum(durations[index:index + 2]) for index in range(0, len(durations), 2)]


def glyphs(text):
    parts = []
    for char in text:
        if unicodedata.category(char).startswith("M") and parts:
            parts[-1] += char
        else:
            parts.append(char)
    return tuple(parts)


class Renderer:
    def __init__(self, font_path, font_index):
        self.font_path = font_path
        self.font_index = font_index
        with TTFont(font_path, fontNumber=font_index, lazy=True) as font:
            self.supported = frozenset(font.getBestCmap())

    @lru_cache(maxsize=112)
    def font(self, size):
        return ImageFont.truetype(self.font_path, size, index=self.font_index)

    def arrange_row(self, font, row, max_panels):
        part_count = min(max_panels, sum(part != " " for part in row))

        @lru_cache(None)
        def choose(start, count):
            if count == 0:
                return (0, ()) if start == len(row) else None
            best = None
            for end in range(start + 1, len(row) - count + 2):
                piece = "".join(row[start:end]).strip()
                if not piece:
                    continue
                left, _, right, _ = font.getbbox(piece)
                width = right - left
                if width > SIZE - 2 * PADDING:
                    continue
                rest = choose(end, count - 1)
                if rest is None:
                    continue
                cost = (SIZE - width) ** 2 + rest[0]
                if best is None or cost < best[0]:
                    best = cost, (piece, *rest[1])
            return best

        solution = choose(0, part_count)
        return solution[1] if solution else None

    def arrange(self, parts, size, max_panels):
        font = self.font(size)
        for count in range(1, min(3, len(parts)) + 1):
            split = [round(len(parts) * i / count) for i in range(count + 1)]
            rows = [self.arrange_row(font, parts[a:b], max_panels) for a, b in zip(split, split[1:])]
            if any(not row for row in rows):
                continue
            bounds = [tuple(font.getbbox(piece) for piece in row) for row in rows]
            tops = [min(box[1] for box in row) for row in bounds]
            heights = [max(box[3] for box in row) - top for row, top in zip(bounds, tops)]
            total = sum(heights) + PADDING * (count - 1)
            height = TIME_TOP if max_panels == 5 and any(4 in PANELS[len(row)] for row in rows) else SIZE
            if total <= height - PADDING * 2:
                return rows, bounds, tops, heights, height, total
        return None

    def draw_text(self, tiles, text, with_sticker):
        if not text:
            return 2 if with_sticker else None
        unsupported = next((char for char in text if ord(char) not in self.supported), None)
        if unsupported:
            raise TextError(f"字体暂不支持字符 {unsupported!r}")
        parts = glyphs(text)
        low, high = 8, SIZE
        layout = None
        while low <= high:
            size = (low + high) // 2
            candidate = self.arrange(parts, size, 4 if with_sticker else 5)
            if candidate:
                layout = size, candidate
                low = size + 1
            else:
                high = size - 1
        if layout is None:
            raise TextError("文字排不进五块屏，请缩短")
        size, (rows, bounds, tops, heights, height, total) = layout
        font = self.font(size)
        sticker_panels = PANELS[max(map(len, rows)) + 1] if with_sticker else None
        y = (height - total) // 2
        for row, boxes, top, height in zip(rows, bounds, tops, heights):
            panels = sticker_panels[-len(row) - 1:-1] if sticker_panels else PANELS[len(row)]
            for screen, piece, (left, _, right, _) in zip(panels, row, boxes):
                x = (SIZE - (right - left)) // 2 - left
                ImageDraw.Draw(tiles[screen]).text((x, y - top), piece, fill=FOREGROUND, font=font)
            y += height + PADDING
        return sticker_panels[-1] if sticker_panels else None

    @lru_cache(maxsize=48)
    def render(self, text, with_sticker=False):
        tiles = [Image.new("RGB", (SIZE, SIZE), BACKGROUND) for _ in range(5)]
        sticker_panel = self.draw_text(tiles, text, with_sticker)
        preview = Image.new("RGB", (SIZE * 5, SIZE), BACKGROUND)
        gifs = []
        blank = Image.new("RGB", (SIZE, SIZE), BACKGROUND)
        for index, tile in enumerate(tiles):
            preview.paste(tile, (SIZE * index, 0))
            output = io.BytesIO()
            tile.save(output, "GIF", save_all=True, append_images=[blank], duration=[750, 250], loop=0)
            gifs.append(output.getvalue())
        preview_bytes = io.BytesIO()
        preview.save(preview_bytes, "PNG", optimize=True)
        return preview_bytes.getvalue(), tuple(gifs), sticker_panel

    def add_timestamp(self, gif, time_label, with_sticker):
        font = self.font(18)
        _, _, right, bottom = font.getbbox(time_label)
        position = (SIZE - PADDING - right, SIZE - PADDING - bottom)
        frames = []
        durations = []
        with Image.open(io.BytesIO(gif)) as source:
            for frame in ImageSequence.Iterator(source):
                tile = frame.convert("RGB")
                if with_sticker:
                    fitted = ImageOps.contain(tile, (SIZE, TIME_TOP))
                    tile = Image.new("RGB", (SIZE, SIZE), BACKGROUND)
                    tile.paste(fitted, ((SIZE - fitted.width) // 2, (TIME_TOP - fitted.height) // 2))
                ImageDraw.Draw(tile).text(position, time_label, font=font, fill=FOREGROUND)
                frames.append(tile.quantize(colors=128))
                durations.append(frame.info.get("duration") or 100)
        output = io.BytesIO()
        frames[0].save(output, "GIF", save_all=True, append_images=frames[1:], duration=durations, loop=0)
        return output.getvalue()
