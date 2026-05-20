import threading
import statistics
import re
from collections import Counter
from enum import Enum, auto
from dataclasses import dataclass
from typing import Callable
from PyQt6.QtCore import QObject, QTimer, pyqtSignal, QRect
from PyQt6.QtWidgets import QApplication
import mss
from PIL import Image, ImageEnhance, ImageOps, ImageChops, ImageFilter


class PreprocessMode(Enum):
    NAME      = auto()   # 3x, grayscale, invert, contrast 2.0 — psm 8, whitelist a-zA-Z-
    WAVE      = auto()   # 4x, binary threshold 160, invert — multi-crop/multi-psm
    DIGIT     = auto()   # 3x, grayscale, invert, contrast 2.0 — psm 8, whitelist 0-9
    HP_TEXT   = auto()   # 3x, grayscale, invert, contrast 2.0 — psm 7, whitelist 0-9/
    MOVE_NAME = auto()   # 3x, grayscale, invert, contrast 2.0 — psm 8, whitelist a-zA-Z space


@dataclass
class OCRBox:
    capture_box: object                   # CaptureBox — only get_capture_rect() is called
    mode: PreprocessMode
    callback: Callable[[str], None]       # called on Qt main thread
    label: str
    enabled: bool = True
    gated: bool = True                    # False = always runs regardless of gate state


class _ServiceSignals(QObject):
    result      = pyqtSignal(str, str)    # (label, text)
    debug       = pyqtSignal(object)      # {"label", "text", "prep": PIL.Image|None}
    gate_changed = pyqtSignal(bool)       # True = gate opened, False = gate closed


class OCRService(QObject):
    def __init__(self, interval_ms: int = 500, parent=None):
        super().__init__(parent)
        self._boxes: list[OCRBox] = []
        self._scanning = False
        self._api = None
        self._use_tesserocr = False
        self._lock = threading.Lock()
        self._signals = _ServiceSignals()
        self._signals.result.connect(self._dispatch_result)

        self._debug_enabled = False

        self._gate_label: str | None = None
        self._gate_open:  bool       = False
        self._gate_miss:  int        = 0

        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._on_timer)

        self._init_engine()

    def set_debug_enabled(self, enabled: bool):
        self._debug_enabled = enabled

    @property
    def gate_changed(self):
        return self._signals.gate_changed

    def set_gate(self, label: str) -> None:
        """Designate a box as the master gate. All other boxes are suppressed
        until this box's OCR result contains 'accur' (case-insensitive)."""
        self._gate_label = label
        self._gate_open  = False
        self._gate_miss  = 0

    def _init_engine(self):
        try:
            from tesserocr import PyTessBaseAPI, OEM, PSM
            from tesseract_path import find_tessdata
            tessdata = find_tessdata()
            self._api = PyTessBaseAPI(path=tessdata, lang="eng", oem=OEM.LSTM_ONLY)
            # Build int→PSM lookup once so _tesseract_call never calls PSM(int)
            self._psm_map = {
                6:  PSM.SINGLE_BLOCK,
                7:  PSM.SINGLE_LINE,
                8:  PSM.SINGLE_WORD,
                13: PSM.RAW_LINE,
            }
            self._use_tesserocr = True
            print("[OCRService] using tesserocr")
        except Exception as e:
            try:
                import pytesseract
                from tesseract_path import find_tesseract
                pytesseract.pytesseract.tesseract_cmd = find_tesseract()
                print(f"[OCRService] tesserocr unavailable ({e}), falling back to pytesseract")
            except Exception as e2:
                print(f"[OCRService] WARNING: no OCR engine available: {e2}")

    def register(self, box: OCRBox) -> None:
        self._boxes.append(box)

    def set_enabled(self, label: str, enabled: bool) -> None:
        for b in self._boxes:
            if b.label == label:
                b.enabled = enabled

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        if self._api is not None:
            try:
                self._api.End()
            except Exception:
                pass

    def _on_timer(self):
        if self._scanning:
            return
        self._scanning = True
        # Snapshot rects on main thread — QWidget geometry must not be read from worker threads
        active = [(b, b.capture_box.get_capture_rect()) for b in self._boxes if b.enabled]
        if not active:
            self._scanning = False
            return
        threading.Thread(target=self._worker_cycle, args=(active,), daemon=True).start()

    def _worker_cycle(self, active: list):
        try:
            dpr = QApplication.primaryScreen().devicePixelRatio()
            rects = [r for _, r in active]
            union = _union_rect(rects)
            monitor = {
                "left":   int(union.left()   * dpr),
                "top":    int(union.top()     * dpr),
                "width":  int(union.width()   * dpr),
                "height": int(union.height()  * dpr),
            }
            with mss.mss() as sct:
                shot = sct.grab(monitor)
            full_img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

            # Separate gate box, ungated boxes, and gated boxes
            gate_pairs    = [(b, r) for b, r in active if b.label == self._gate_label]
            ungated_pairs = [(b, r) for b, r in active if b.label != self._gate_label and not b.gated]
            gated_pairs   = [(b, r) for b, r in active if b.label != self._gate_label and b.gated]

            # Process gate box and update open/closed state.
            # gate_live reflects this cycle's reading; _gate_open is buffered
            # (3-miss) for stable badge display only.
            gate_live = False
            if gate_pairs:
                gate_text = self._ocr_one(full_img, union, dpr, *gate_pairs[0])
                gate_live = bool(gate_text and "accur" in gate_text.lower())
                if gate_live:
                    self._gate_miss = 0
                    if not self._gate_open:
                        self._gate_open = True
                        self._signals.gate_changed.emit(True)
                else:
                    self._gate_miss += 1
                    if self._gate_miss >= 3 and self._gate_open:
                        self._gate_open = False
                        self._signals.gate_changed.emit(False)

            # Ungated boxes always run
            for pair in ungated_pairs:
                self._ocr_one(full_img, union, dpr, *pair)

            # Gated boxes run only when the gate reads live this cycle — no lag.
            if self._gate_label and not gate_live:
                return

            for pair in gated_pairs:
                self._ocr_one(full_img, union, dpr, *pair)

        except Exception as e:
            print(f"[OCRService] worker error: {e}")
        finally:
            self._scanning = False

    def _ocr_one(self, full_img, union, dpr, box, rect) -> str:
        """Crop, blank-guard, OCR, and emit for a single box. Returns the text (or '')."""
        left   = int((rect.left()   - union.left()) * dpr)
        top    = int((rect.top()    - union.top())  * dpr)
        right  = min(int((rect.right()  - union.left()) * dpr), full_img.width)
        bottom = min(int((rect.bottom() - union.top())  * dpr), full_img.height)
        if right <= left or bottom <= top:
            return ""
        crop = full_img.crop((left, top, right, bottom))

        threshold = 8 if box.mode == PreprocessMode.WAVE else 25
        try:
            stdev = statistics.stdev(crop.convert("L").getdata())
        except Exception:
            stdev = 0
        if stdev < threshold:
            self._signals.result.emit(box.label, "")
            if self._debug_enabled:
                prep = _preprocess(crop, box.mode) if box.mode != PreprocessMode.WAVE else None
                self._signals.debug.emit({"label": box.label, "text": "(blank)", "raw": crop, "prep": prep})
            return ""

        text, prep = self._run_ocr(crop, box.mode)
        if text is not None:
            self._signals.result.emit(box.label, text)
        if self._debug_enabled:
            self._signals.debug.emit({"label": box.label, "text": text or "", "raw": crop, "prep": prep})
        return text or ""

    def _dispatch_result(self, label: str, text: str):
        for box in self._boxes:
            if box.label == label and box.enabled:
                box.callback(text)
                break

    def _run_ocr(self, img: Image.Image, mode: PreprocessMode):
        if mode == PreprocessMode.WAVE:
            return self._ocr_wave(img)
        prep = _preprocess(img, mode)
        return self._ocr_simple(prep, mode), prep

    def _ocr_wave(self, img: Image.Image):
        # Wave digits are white on normal waves and RED on every 10th (boss) wave.
        # _white_or_red_text_mask handles both via channel-based extraction.
        text_mask = _white_or_red_text_mask(img)
        binary = ImageOps.invert(text_mask)  # text pixels → black
        prep = binary.resize((img.width * 3, img.height * 3), Image.BILINEAR)
        prep = ImageOps.expand(prep, border=20, fill=255)
        # Morphological "opening" alternative: erode-then-dilate (MaxFilter then
        # MinFilter, since text is dark on light). This thins thin features like
        # the slash through pixel-font zeros — Tesseract sees a slashed 0 as an 8,
        # so a variant with the slash removed sometimes votes the right answer.
        opened = prep.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))

        # Run multi-PSM × multi-whitelist on both variants and vote.
        # Tie-breaking: prefer longer digit strings (3 digits > 2 > 1), then most votes.
        votes: Counter = Counter()
        raw_for: dict = {}
        for variant in (prep, opened):
            for psm in [13, 7, 8]:
                for wl in ["0123456789", ""]:
                    raw = self._tesseract_call(variant, psm=psm, whitelist=wl)
                    if not raw:
                        continue
                    digits = re.sub(r'[^0-9]', '', raw)[:3]
                    if digits:
                        val = int(digits)
                        if 1 <= val <= 200:
                            votes[digits] += 1
                            raw_for[digits] = raw
        if votes:
            best_digits = max(votes.keys(), key=lambda d: (len(d), votes[d]))
            return raw_for[best_digits], prep
        return "", prep

    def _ocr_simple(self, img: Image.Image, mode: PreprocessMode) -> str | None:
        if mode == PreprocessMode.HP_TEXT:
            # Try all PSMs and keep the longest all-digit result.
            # PSM 7/8 can drop leading characters at the left edge; PSM 13 (raw line)
            # is most permissive. Taking the longest result picks "243" over "43".
            best = None
            for psm in [13, 7, 8]:
                text = self._tesseract_call(img, psm=psm, whitelist="0123456789")
                digits = "".join(c for c in (text or "") if c.isdigit())
                if digits:
                    if best is None or len(digits) > len(best):
                        best = digits
            return best

        psm_map = {
            PreprocessMode.NAME:      8,
            PreprocessMode.DIGIT:     8,
            PreprocessMode.MOVE_NAME: 8,
        }
        whitelist_map = {
            PreprocessMode.NAME:      "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-",
            PreprocessMode.DIGIT:     "0123456789",
            PreprocessMode.MOVE_NAME: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ ",
        }
        return self._tesseract_call(img, psm=psm_map[mode], whitelist=whitelist_map[mode])

    def _tesseract_call(self, img: Image.Image, psm: int, whitelist: str) -> str | None:
        if self._use_tesserocr:
            try:
                with self._lock:
                    # PSM and variables must be set before SetImage
                    self._api.SetPageSegMode(self._psm_map[psm])
                    self._api.SetVariable("tessedit_char_whitelist", whitelist)
                    self._api.SetImage(img)
                    text = self._api.GetUTF8Text().strip()
                    self._api.Clear()
                    return text
            except Exception as e:
                print(f"[OCRService] tesserocr error: {e}")
                return None
        else:
            try:
                import pytesseract
                wl_flag = f"-c tessedit_char_whitelist={whitelist}" if whitelist else ""
                return pytesseract.image_to_string(
                    img,
                    config=f"--psm {psm} --oem 3 {wl_flag}".strip(),
                ).strip()
            except Exception as e:
                print(f"[OCRService] pytesseract error: {e}")
                return None


def _white_or_red_text_mask(img: Image.Image) -> Image.Image:
    """Binary text mask capturing both white and red-orange text on a dark
    background. Shared by wave OCR and DIGIT (level) OCR — pokerogue uses red
    digits to signal special states (boss waves, max level) and grayscale-based
    thresholding loses too much red signal to work reliably.

    Returns an L-mode image where text pixels are white (255) and background
    pixels are black (0). Caller is expected to invert for Tesseract.
    """
    rgb = img.convert("RGB")
    r, g, b = rgb.split()
    # White text: all channels bright.
    white_r = r.point(lambda p: 255 if p > 200 else 0)
    white_g = g.point(lambda p: 255 if p > 200 else 0)
    white_b = b.point(lambda p: 255 if p > 200 else 0)
    white_mask = ImageChops.multiply(ImageChops.multiply(white_r, white_g), white_b)
    # Red/orange text: red dominates G and B by a comfortable margin.
    r_bright = r.point(lambda p: 255 if p > 150 else 0)
    rg_diff  = ImageChops.subtract(r, g)
    rb_diff  = ImageChops.subtract(r, b)
    rg_dom   = rg_diff.point(lambda p: 255 if p > 50 else 0)
    rb_dom   = rb_diff.point(lambda p: 255 if p > 50 else 0)
    red_mask = ImageChops.multiply(ImageChops.multiply(r_bright, rg_dom), rb_dom)
    return ImageChops.lighter(white_mask, red_mask)


def _preprocess(img: Image.Image, mode: PreprocessMode) -> Image.Image:
    if mode == PreprocessMode.HP_TEXT:
        # Pixel-font digits: NEAREST preserves crisp edges; binary threshold removes grey fringe
        img = img.resize((img.width * 4, img.height * 4), Image.NEAREST)
        img = img.convert("L")
        img = ImageOps.invert(img)
        img = img.point(lambda p: 255 if p > 80 else 0)   # hard binary threshold
        img = ImageOps.expand(img, border=20, fill=255)
        return img
    if mode == PreprocessMode.DIGIT:
        # Level digits flip to red-orange at max-level-for-floor. Mask by color
        # so red and white digits both produce clean binary input for Tesseract.
        text_mask = _white_or_red_text_mask(img)
        binary = ImageOps.invert(text_mask)  # text → black, bg → white
        prep = binary.resize((img.width * 3, img.height * 3), Image.BILINEAR)
        return ImageOps.expand(prep, border=20, fill=255)
    img = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
    img = img.convert("L")
    img = ImageOps.invert(img)
    img = ImageEnhance.Contrast(img).enhance(2.0)
    return img


def _union_rect(rects: list) -> QRect:
    u = rects[0]
    for r in rects[1:]:
        u = u.united(r)
    return u
