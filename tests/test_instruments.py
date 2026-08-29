from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tradingbot.instruments import INSTRUMENTS, LeverageState, leverage_of


SOURCE_ROOT = Path(__file__).parents[1] / "src" / "tradingbot"


def _assigned_names(node: ast.Assign | ast.AnnAssign) -> tuple[str, ...]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return tuple(target.id for target in targets if isinstance(target, ast.Name))


def _is_collection(value: ast.expr | None) -> bool:
    if isinstance(
        value,
        (
            ast.Dict,
            ast.DictComp,
            ast.List,
            ast.ListComp,
            ast.Set,
            ast.SetComp,
            ast.Tuple,
        ),
    ):
        return True
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id in {"dict", "frozenset", "list", "set", "tuple"}
    )


def test_only_one_module_defines_leverage():
    offenders: list[str] = []

    for path in SOURCE_ROOT.rglob("*.py"):
        if path == SOURCE_ROOT / "instruments.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if _is_collection(value) and any(
                "LEVERAG" in name.upper() for name in _assigned_names(node)
            ):
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}:{node.lineno}")

    assert offenders == [], f"leverage collections outside instruments.py: {offenders}"


@pytest.mark.parametrize(
    ("symbol", "expected_leverage", "expected_kind"),
    [
        ("FNGU", 3.0, "etn"),
        ("GGLL", 2.0, "single_stock_levered"),
        ("SOXL", 3.0, "etf"),
        ("TECL", 3.0, "etf"),
    ],
)
def test_every_held_symbol_resolves_in_the_registry(
    symbol: str, expected_leverage: float, expected_kind: str
):
    instrument = INSTRUMENTS[symbol]

    assert instrument.symbol == symbol
    assert leverage_of(symbol) == expected_leverage
    assert instrument.kind == expected_kind
    assert instrument.expense_ratio is None
    assert instrument.benchmark_index is None


def test_spcx_resolves_to_an_explicit_unknown_leverage():
    instrument = INSTRUMENTS["SPCX"]

    assert instrument.symbol == "SPCX"
    assert instrument.kind == "etf"
    assert instrument.leverage is LeverageState.UNKNOWN
    assert leverage_of(" spcx ") is LeverageState.UNKNOWN


def test_unknown_leverage_cannot_silently_fall_back_to_one():
    with pytest.raises(TypeError, match="handle LeverageState.UNKNOWN explicitly"):
        _ = leverage_of("SPCX") or 1.0


def test_ggll_records_that_its_multiple_has_not_been_verified_against_googl():
    assert (
        INSTRUMENTS["GGLL"].leverage_verification_note
        == "unverified against GOOGL; underlying history is not cached"
    )


def test_unknown_symbol_does_not_silently_default_to_one():
    with pytest.raises(KeyError, match="refusing to assume 1.0 leverage"):
        leverage_of("NEWLY_BOUGHT_PRODUCT")
