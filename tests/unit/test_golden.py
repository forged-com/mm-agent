import pytest

from tests.contract import check
from tests.fixtures import golden


def test_expected_hashes_match_the_canonical_rows() -> None:
    assert golden.compute() == golden.expected()


def test_every_job_lands_rows() -> None:
    for files in golden.expected()["files"].values():
        assert all(files[name]["rows"] > 0 for name in golden.MAPPED)


def test_window_spans_a_utc_midnight_and_the_berlin_fold() -> None:
    assert golden.START < golden.UTC_MIDNIGHT < golden.END
    assert golden.START < golden.FOLD_START < golden.FOLD_END < golden.END
    assert golden.expected()["window"] == {"from": golden.START, "to": golden.END}


def test_no_timestamp_falls_in_the_repeated_local_hour() -> None:
    stamps = [row["placed"] for row in golden.orders()]
    stamps += [row["ended"] for row in golden.orders() if row["ended"] is not None]
    stamps += [row["ts"] for row in golden.fills()]
    assert any(golden.START <= stamp < golden.FOLD_START for stamp in stamps)
    assert any(golden.FOLD_END <= stamp for stamp in stamps)
    assert not any(golden.FOLD_START <= stamp < golden.FOLD_END for stamp in stamps)


def test_rows_outside_the_window_exist_on_both_sides() -> None:
    assert any(row["ended"] is not None and row["ended"] < golden.START for row in golden.orders())
    assert any(row["placed"] >= golden.END for row in golden.orders())
    assert any(row["ts"] < golden.START for row in golden.fills())
    assert any(row["ts"] >= golden.END for row in golden.fills())


def test_every_fill_references_an_order_of_the_same_pair() -> None:
    ids = {(row["pair"], row["id"]) for row in golden.orders()}
    assert all((row["pair"], row["order"]) in ids for row in golden.fills())


@pytest.mark.parametrize("name", ["order", "fill"])
def test_canonical_rows_satisfy_the_contract(name: str) -> None:
    rows = golden.orders() if name == "order" else golden.fills()
    fields = golden.ORDER_FIELDS if name == "order" else golden.FILL_FIELDS
    for row in rows:
        record = {field: row[field] for field in fields}
        if name == "fill":
            record["id"] = "0" * 64
        check(name, record)
