from pathlib import Path

_SRC_PACKAGE = Path(__file__).resolve().parent.parent / "src" / "tradingbot"
if _SRC_PACKAGE.exists():
    __path__.append(str(_SRC_PACKAGE))
