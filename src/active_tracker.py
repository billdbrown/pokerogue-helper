import difflib
from PyQt6.QtCore import QObject, pyqtSignal
import stats_db


def _resolve_name(query: str, names: list) -> str | None:
    prefix = [n for n in names if n.startswith(query + "-")]
    if prefix:
        return min(prefix, key=len)
    matches = difflib.get_close_matches(query, names, n=1, cutoff=0.6)
    return matches[0] if matches else None


class _Signals(QObject):
    active_changed = pyqtSignal(str)  # normalized API name, or "" to clear


_TYPE_MISS_LIMIT = 4  # ~2 s of no type badge before suppressing name reads


class ActiveTracker:
    def __init__(self, capture_box=None):
        self._current = ""
        self._miss_count = 0
        self._type_valid = False
        self._type_miss  = 0
        self._signals = _Signals()
        self.active_changed = self._signals.active_changed

    def notify_type(self, has_type: bool) -> None:
        """Called each OCR cycle with whether the player-type badge is visible."""
        if has_type:
            self._type_valid = True
            self._type_miss  = 0
        else:
            self._type_miss += 1
            if self._type_miss >= _TYPE_MISS_LIMIT:
                self._type_valid = False

    def receive_ocr_result(self, text: str) -> None:
        """Called by OCRService on the Qt main thread."""
        cleaned = text.strip().lower().replace("♀", "").replace("♂", "").replace("'", "")
        name = "".join(c for c in cleaned if c.isalpha() or c == "-")

        if len(name) < 3:
            name = ""

        if name and stats_db.is_ready():
            known = stats_db.all_names()
            matched = _resolve_name(name, known)
            name = matched or ""

        if name:
            if not self._type_valid:
                return  # type badge not visible — likely a menu, suppress
            self._miss_count = 0
            if name != self._current:
                self._current = name
                self._signals.active_changed.emit(name)
        else:
            self._miss_count += 1
            if self._miss_count >= 3 and self._current:
                self._current = ""
                self._signals.active_changed.emit("")
