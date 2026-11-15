"""One measured calculated-artifact profile reaches every full-result boundary."""
from genshin_app import (
    calculation_assistance,
    calculation_delivery_files,
    calculation_review,
    review_batch_json,
)
from genshin_app.agent_chat import native_document_restore_points, sessions
from genshin_app.calculation_capacity import (
    CALCULATED_ARTIFACT_BYTES,
    CALCULATION_DELIVERY_TOTAL_BYTES,
    GUI_DOCUMENT_TRANSPORT_BYTES,
)
from genshin_app.gui import document_transport


def test_measured_capacity_profile_is_shared_by_full_result_consumers():
    assert CALCULATED_ARTIFACT_BYTES == 96 * 1024 * 1024
    assert CALCULATION_DELIVERY_TOTAL_BYTES == 192 * 1024 * 1024
    assert GUI_DOCUMENT_TRANSPORT_BYTES == 192 * 1024 * 1024
    assert calculation_delivery_files.LIMITS["calculated_bytes"] == CALCULATED_ARTIFACT_BYTES
    assert calculation_delivery_files.LIMITS["total_bytes"] == CALCULATION_DELIVERY_TOTAL_BYTES
    assert sessions.MAX_CALCULATED == CALCULATED_ARTIFACT_BYTES
    assert calculation_review.MAX_FILE_BYTES == CALCULATED_ARTIFACT_BYTES
    assert review_batch_json.SNAPSHOT_BYTES == CALCULATED_ARTIFACT_BYTES
    assert calculation_assistance.MAX_BYTES == CALCULATED_ARTIFACT_BYTES
    assert native_document_restore_points.MAX_DOCUMENT_BYTES == CALCULATED_ARTIFACT_BYTES
    assert native_document_restore_points.MAX_APPLICATION_PAYLOAD_BYTES == CALCULATION_DELIVERY_TOTAL_BYTES
    assert native_document_restore_points.MAX_STRUCTURE_NODES == 8_000_000
    assert native_document_restore_points.MAX_STRUCTURE_DEPTH == 64
    assert document_transport.MAX_BYTES == GUI_DOCUMENT_TRANSPORT_BYTES


def test_delivery_and_review_keep_independent_bounded_headroom():
    limits = calculation_delivery_files.LIMITS
    maximum_default_artifacts = (
        limits["native_plan_bytes"]
        + limits["calculated_bytes"]
        + 2 * limits["workbook_bytes"]
    )
    assert maximum_default_artifacts <= limits["total_bytes"]
    assert review_batch_json.SNAPSHOT_NODES == 8_000_000
    assert review_batch_json.SNAPSHOT_WORK == 24_000_000
    assert review_batch_json.PROJECTION_WORK == 8_000_000
    assert review_batch_json.BATCH_BYTES == 2 * 1024 * 1024
