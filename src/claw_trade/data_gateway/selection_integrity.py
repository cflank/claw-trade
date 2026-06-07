from __future__ import annotations

from claw_trade.data_gateway.warehouse.selection_columnar import SelectionColumnarWarehouse


def validate_selection_columnar_manifest_ref(manifest_ref: str, *, expected_sha256: str | None = None) -> bool:
    return SelectionColumnarWarehouse.default().validate_manifest_ref(
        manifest_ref,
        expected_sha256=expected_sha256,
    )
