# SPDX-License-Identifier: AGPL-3.0-only
import numpy as np
import pytest

from backend.services.audio_separation_service import _chunk_plan, _crossfade


def test_demucs_chunk_plan_covers_track_without_owned_gaps():
    plan = _chunk_plan(total_frames=100, chunk_frames=40, overlap_frames=10)
    assert plan == [(0, 40, 50), (40, 80, 90), (80, 100, 100)]
    assert plan[0][0] == 0
    assert plan[-1][1] == 100
    for previous, current in zip(plan, plan[1:]):
        assert previous[1] == current[0]


def test_demucs_chunk_plan_rejects_invalid_sizes():
    with pytest.raises(ValueError):
        _chunk_plan(100, 0, 10)
    with pytest.raises(ValueError):
        _chunk_plan(100, 40, -1)


def test_crossfade_preserves_shape_and_moves_from_tail_to_head():
    tail = np.ones((4, 2), dtype=np.float32)
    head = np.zeros((4, 2), dtype=np.float32)
    got = _crossfade(tail, head)
    assert got.shape == head.shape
    assert got[0, 0] == pytest.approx(1.0)
    assert got[-1, 0] < got[0, 0]
    assert got[-1, 0] > 0.0
