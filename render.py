import io
import unicodedata
from functools import lru_cache

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont


SIZE = 128
PADDING = 5
MAX_TEXT = 48
BACKGROUND = (3, 8, 13)
FOREGROUND = (244, 252, 255)
PANELS = {1: (2,), 2: (1, 3), 3: (1, 2, 3), 4: (0, 1, 3, 4), 5: (0, 1, 2, 3, 4)}


class TextError(ValueError):
    pass


def normalize_text(value):
    if not isinstance(value, str):
        raise TextError("请输入文字")
    text = " ".join(unicodedata.normalize("NFC", value).split())
    if not text:
        raise TextError("请输入文字")
    if len(text) > MAX_TEXT:
        raise TextError(f"最多输入 {MAX_TEXT} 个字符")
    return text


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

    def arrange_row(self, font, row):
        part_count = min(5, sum(part != " " for part in row))

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

    def arrange(self, parts, size):
        font = self.font(size)
        for count in range(1, min(3, len(parts)) + 1):
            split = [round(len(parts) * i / count) for i in range(count + 1)]
            rows = [self.arrange_row(font, parts[a:b]) for a, b in zip(split, split[1:])]
            if any(not row for row in rows):
                continue
            bounds = [tuple(font.getbbox(piece) for piece in row) for row in rows]
            tops = [min(box[1] for box in row) for row in bounds]
            heights = [max(box[3] for box in row) - top for row, top in zip(bounds, tops)]
            total = sum(heights) + PADDING * (count - 1)
            if total <= SIZE - PADDING * 2:
                return rows, bounds, tops, heights, total
        return None

    @lru_cache(maxsize=48)
    def render(self, text):
        unsupported = next((char for char in text if ord(char) not in self.supported), None)
        if unsupported:
            raise TextError(f"字体暂不支持字符 {unsupported!r}")
        parts = glyphs(text)
        low, high = 8, SIZE
        layout = None
        while low <= high:
            size = (low + high) // 2
            candidate = self.arrange(parts, size)
            if candidate:
                layout = size, candidate
                low = size + 1
            else:
                high = size - 1
        if layout is None:
            raise TextError("文字排不进五块屏，请缩短")
        size, (rows, bounds, tops, heights, total) = layout
        font = self.font(size)
        tiles = [Image.new("RGB", (SIZE, SIZE), BACKGROUND) for _ in range(5)]
        y = (SIZE - total) // 2
        for row, boxes, top, height in zip(rows, bounds, tops, heights):
            for screen, piece, (left, _, right, _) in zip(PANELS[len(row)], row, boxes):
                x = (SIZE - (right - left)) // 2 - left
                ImageDraw.Draw(tiles[screen]).text((x, y - top), piece, fill=FOREGROUND, font=font)
            y += height + PADDING
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
        return preview_bytes.getvalue(), tuple(gifs)
