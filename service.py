import base64
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from render import MAX_STICKER_BYTES, Renderer, StickerError, TextError, normalize_text, sticker_gif


DISPLAY_SECONDS = 30 * 60
ASSET_TIMEOUT = 12
RESTORE_RETRY_SECONDS = 5
MAX_STORED_STICKERS = 12
CHANNEL_INFO_URL = "https://app.divoom-gz.com/Channel/Get5LcdInfoV2"
ROOT = Path(__file__).resolve().parent
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
ASSET_PATH = re.compile(r"/asset/([a-f0-9]{32})/([0-4])\.gif")
STATIC = {"/": ("index.html", "text/html"), "/style.css": ("style.css", "text/css"),
          "/app.js": ("app.js", "text/javascript")}
NOTICE_TIME_ZONE = ZoneInfo("Asia/Shanghai")


class DeviceError(RuntimeError):
    pass


def post_json(url, payload, timeout=4):
    request = urllib.request.Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
    with HTTP.open(request, timeout=timeout) as response:
        return json.load(response)


class Gate:
    def __init__(self, endpoint, token):
        self.endpoint = endpoint
        self.token = token

    def call(self, command, **fields):
        data = post_json(self.endpoint, {"Command": command, "LocalToken": self.token, **fields})
        if data.get("ReturnCode", data.get("error_code")) != 0:
            raise DeviceError(f"{command} rejected by device")
        return data

    def layout(self):
        ident = self.call("Channel/GetAllConf").get("DeviceId")
        if type(ident) is not int or ident < 1:
            raise DeviceError("Device identity unavailable")
        data = post_json(CHANNEL_INFO_URL, {"DeviceId": ident, "DeviceType": "LCD"})
        if data.get("ReturnCode") != 0 or data.get("DeviceId") != ident:
            raise DeviceError("Saved display layout unavailable")
        mode = data.get("ChannelType")
        if mode == 0 and type(data.get("ClockId")) is int and data["ClockId"] > 0:
            return {"ClockId": data["ClockId"]}
        group = data.get("LcdIndependence")
        if mode == 1 and type(group) is int and group > 0:
            if any(row.get("LcdIndependence") == group for row in data.get("LcdIndependenceList", [])):
                return {"LcdIndependence": group}
        raise DeviceError("Current display layout cannot be restored")

    def play(self, urls):
        self.call("Device/PlayGifLCDs", **{f"LCD{index}GifFile": [url] for index, url in enumerate(urls)})

    def breathe(self, color):
        self.call("Channel/SetRGBInfo", OnOff=1, Brightness=100, Color=color, ColorCycle=0,
                  SelectLightIndex=0, LightList=[{"SelectEffect": effect} for effect in (4, 0, 0)])

    def restore(self, layout):
        if "ClockId" in layout:
            self.call("Channel/Set5LcdWholeClockId", ClockId=layout["ClockId"])
        else:
            self.call("Channel/Set5LcdChannelType", ChannelType=1, LcdIndependence=layout["LcdIndependence"])


@dataclass
class Notice:
    color: str
    gifs: tuple
    key: str = field(default_factory=lambda: secrets.token_hex(16))
    fetched: set = field(default_factory=set)


class Notices:
    def __init__(self, gate, assets_origin, journal):
        self.gate = gate
        self.assets_origin = assets_origin.rstrip("/")
        self.journal = journal
        self.lock = threading.Condition()
        self.active = None
        self.pending = None
        self.state = {"phase": "idle"}
        if journal.exists():
            self.pending = json.loads(journal.read_bytes())
            self.state = {"phase": "recovering", "id": self.pending.get("id"),
                          "sentTime": self.pending.get("sentTime"), "readRequested": self.pending.get("read", False),
                          "deadline": self.pending["deadline"]}
            threading.Thread(target=self.finish, args=("restored",), daemon=True).start()

    def persist(self, pending):
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.journal.with_suffix(".tmp")
        temporary.write_text(json.dumps(pending))
        temporary.replace(self.journal)

    def start(self, color, gifs, sent_time):
        notice = Notice(color, gifs)
        with self.lock:
            if self.active is not None or self.pending is not None:
                return None
            self.active = notice
            self.state = {"phase": "preparing", "id": notice.key, "sentTime": sent_time}
            snapshot = dict(self.state)
        threading.Thread(target=self.run, args=(notice,), daemon=True).start()
        return snapshot

    def request_restore(self, key):
        with self.lock:
            if key is not None and key != self.state.get("id"):
                return None
            if self.state["phase"] == "read":
                return dict(self.state)
            if self.active is None and self.pending is None:
                return None
            if not self.state.get("readRequested"):
                if self.pending is not None:
                    pending = {**self.pending, "read": True, "deadline": time.time()}
                    self.persist(pending)
                    self.pending = pending
                self.state.update(phase="restoring", readRequested=True)
                self.lock.notify_all()
            return dict(self.state)

    def complete(self, outcome):
        self.active = self.pending = None
        self.state = {"phase": outcome, "id": self.state.get("id"), "sentTime": self.state.get("sentTime")}

    def status(self):
        with self.lock:
            result = dict(self.state)
            if self.active is not None:
                result["fetched"] = len(self.active.fetched)
            return result

    def asset(self, key, index):
        with self.lock:
            if self.active is not None and not self.state.get("readRequested") and hmac.compare_digest(self.active.key, key):
                return self.active.gifs[index]
        return None

    def fetched(self, key, index):
        with self.lock:
            if self.active is not None and hmac.compare_digest(self.active.key, key):
                self.active.fetched.add(index)
                if len(self.active.fetched) == 5:
                    self.lock.notify_all()

    def run(self, notice):
        try:
            layout = self.gate.layout()
            with self.lock:
                if self.state.get("readRequested"):
                    self.complete("read")
                    return
                pending = {"layout": layout, "deadline": time.time(), "id": notice.key, "sentTime": self.state["sentTime"]}
                self.persist(pending)
                self.pending = pending
        except Exception:
            logging.exception("Could not save the current display layout")
            with self.lock:
                self.complete("read" if self.state.get("readRequested") else "layout-error")
            return

        outcome = "send-error"
        try:
            urls = [f"{self.assets_origin}/asset/{notice.key}/{index}.gif" for index in range(5)]
            self.gate.play(urls)
            with self.lock:
                if not self.state.get("readRequested"):
                    self.state["phase"] = "downloading"
                if not self.lock.wait_for(lambda: self.state.get("readRequested") or len(notice.fetched) == 5, ASSET_TIMEOUT):
                    raise DeviceError(f"{len(notice.fetched)} of 5 animations fetched")
                if not self.state.get("readRequested"):
                    pending = {**self.pending, "deadline": time.time() + DISPLAY_SECONDS}
                    self.persist(pending)
                    self.pending = pending
                    self.state.update(phase="playing", deadline=pending["deadline"])
                    outcome = "restored"
            if outcome == "restored":
                logging.info("Device fetched all five GIFs")
                try:
                    self.gate.breathe(notice.color)
                    logging.info("Device accepted ambient lighting")
                except Exception:
                    logging.exception("Could not apply ambient lighting")
                    outcome = "light-error"
                    with self.lock:
                        self.state["lightError"] = True
        except Exception:
            with self.lock:
                if not self.state.get("readRequested"):
                    logging.exception("Could not start five-screen playback")
        self.finish(outcome)

    def finish(self, outcome):
        with self.lock:
            self.lock.wait_for(lambda: self.state.get("readRequested"), max(0, self.pending["deadline"] - time.time()))
            self.state["phase"] = "restoring"
            layout = self.pending["layout"]
        while True:
            try:
                self.gate.restore(layout)
                with self.lock:
                    self.journal.unlink(missing_ok=True)
                    self.complete("read" if self.state.get("readRequested") else outcome)
            except Exception:
                logging.exception("Display restore failed; retry scheduled")
                with self.lock:
                    self.state["phase"] = "restore-retry"
                time.sleep(RESTORE_RETRY_SECONDS)
                continue
            logging.info("Device accepted display restoration")
            return


class Server(ThreadingHTTPServer):
    def __init__(self, address, renderer, notices, access_key, origin):
        self.renderer = renderer
        self.notices = notices
        self.access_key = access_key
        self.origin = origin
        self.stickers = OrderedDict()
        self.sticker_lock = threading.Lock()
        super().__init__(address, Handler)

    def store_sticker(self, gif):
        key = secrets.token_hex(16)
        with self.sticker_lock:
            self.stickers[key] = gif
            if len(self.stickers) > MAX_STORED_STICKERS:
                self.stickers.popitem(last=False)
        return key

    def get_sticker(self, key):
        with self.sticker_lock:
            gif = self.stickers.get(key)
            if gif is not None:
                self.stickers.move_to_end(key)
            return gif


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def authenticated(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except ValueError:
            return False
        session = cookie.get("gate_session")
        return bool(session and hmac.compare_digest(session.value.encode(), self.server.access_key.encode()))

    def reply(self, status, body=b"", content_type="text/plain", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def json_reply(self, status, data):
        self.reply(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        if self.path == "/healthz":
            self.json_reply(200, {"ok": True})
            return
        match = ASSET_PATH.fullmatch(self.path)
        if match:
            key, panel = match[1], int(match[2])
            gif = self.server.notices.asset(key, panel)
            if gif is None:
                self.reply(404)
            else:
                self.reply(200, gif, "image/gif")
                self.server.notices.fetched(key, panel)
            return
        if hmac.compare_digest(self.path.encode(), f"/open/{self.server.access_key}".encode()):
            self.reply(303, headers={"Location": "/", "Set-Cookie":
                       f"gate_session={self.server.access_key}; Path=/; HttpOnly; SameSite=Strict"})
            return
        if not self.authenticated():
            self.reply(401, "请使用专用访问链接打开时光之门。".encode(), "text/plain; charset=utf-8")
            return
        if self.path == "/api/status":
            self.json_reply(200, self.server.notices.status())
            return
        if self.path == "/favicon.ico":
            self.reply(204)
            return
        entry = STATIC.get(self.path)
        if entry:
            filename, content_type = entry
            self.reply(200, (ROOT / "web" / filename).read_bytes(), f"{content_type}; charset=utf-8")
        else:
            self.reply(404)

    def do_POST(self):
        if self.path not in ("/api/preview", "/api/send", "/api/sticker", "/api/restore"):
            self.reply(404)
            return
        if not self.authenticated():
            self.json_reply(401, {"error": "请重新用访问链接打开页面"})
            return
        if self.headers.get("Origin") != self.server.origin:
            self.json_reply(403, {"error": "请求来源不匹配"})
            return
        if self.path == "/api/sticker":
            self.upload_sticker()
            return
        if self.path == "/api/restore":
            self.restore_notice()
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2048:
                raise ValueError
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError
            text = normalize_text(payload.get("text", ""))
            color = payload.get("color")
            if not isinstance(color, str) or not re.fullmatch(r"#[a-fA-F0-9]{6}", color):
                raise ValueError
            key = payload.get("sticker")
            sticker = None
            if key is not None:
                if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{32}", key):
                    raise StickerError("请重新选择表情包")
                sticker = self.server.get_sticker(key)
                if sticker is None:
                    raise StickerError("表情包已过期，请重新选择")
            if not text and sticker is None:
                raise TextError("请输入文字或选择表情包")
            preview, gifs, panel = self.server.renderer.render(text, with_sticker=sticker is not None)
        except (StickerError, TextError) as error:
            self.json_reply(400, {"error": str(error)})
            return
        except (UnicodeError, ValueError):
            self.json_reply(400, {"error": "文字或颜色格式无效"})
            return
        if self.path == "/api/preview":
            encoded = base64.b64encode(preview).decode()
            self.json_reply(200, {"image": f"data:image/png;base64,{encoded}", "stickerPanel": panel})
            return
        if sticker is not None:
            gifs = gifs[:panel] + (sticker,) + gifs[panel + 1:]
        sent_time = datetime.now(NOTICE_TIME_ZONE).strftime("%H:%M")
        last = self.server.renderer.add_timestamp(gifs[-1], sent_time, with_sticker=panel == 4)
        snapshot = self.server.notices.start(color, (*gifs[:-1], last), sent_time)
        if snapshot is not None:
            self.json_reply(HTTPStatus.ACCEPTED, snapshot)
        else:
            self.json_reply(HTTPStatus.CONFLICT, {"error": "上一条内容尚未结束"})

    def restore_notice(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 512:
                raise ValueError
            payload = json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(payload, dict):
                raise ValueError
            key = payload.get("id")
            if key is not None and (not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{32}", key)):
                raise ValueError
        except (UnicodeError, ValueError):
            self.json_reply(400, {"error": "通知编号格式无效"})
            return
        try:
            snapshot = self.server.notices.request_restore(key)
        except OSError:
            logging.exception("Could not save restore request")
            self.json_reply(503, {"error": "暂时无法保存恢复请求，请重试"})
            return
        if snapshot is None:
            self.json_reply(HTTPStatus.CONFLICT, {"error": "这条通知已结束或当前没有通知"})
        else:
            self.json_reply(HTTPStatus.OK if snapshot["phase"] == "read" else HTTPStatus.ACCEPTED, snapshot)

    def upload_sticker(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_STICKER_BYTES:
                self.json_reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "表情包最大 4 MB"})
                return
            if length <= 0:
                raise StickerError("请选择表情包文件")
            gif = sticker_gif(self.rfile.read(length))
        except ValueError as error:
            message = str(error) if isinstance(error, StickerError) else "表情包格式无效"
            self.json_reply(400, {"error": message})
            return
        key = self.server.store_sticker(gif)
        encoded = base64.b64encode(gif).decode()
        self.json_reply(201, {"id": key, "image": f"data:image/gif;base64,{encoded}"})


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    port = int(os.environ.get("GATE_PORT", "19001"))
    origin = os.environ["GATE_ORIGIN"].rstrip("/")
    assets_origin = os.environ.get("GATE_ASSET_ORIGIN", origin)
    access_key = os.environ["GATE_ACCESS_KEY"]
    if not re.fullmatch(r"[a-f0-9]{32,128}", access_key) or not urlsplit(origin).netloc:
        raise SystemExit("Invalid control origin or access key")
    gate = Gate(os.environ["GATE_ENDPOINT"], int(os.environ["GATE_TOKEN"]))
    renderer = Renderer(os.environ["GATE_FONT"], int(os.environ.get("GATE_FONT_INDEX", "2")))
    notices = Notices(gate, assets_origin, Path(os.environ["GATE_JOURNAL"]))
    with Server((os.environ.get("GATE_LISTEN", "127.0.0.1"), port), renderer, notices, access_key, origin) as server:
        logging.info("Listening on port %d", port)
        server.serve_forever()


if __name__ == "__main__":
    main()
