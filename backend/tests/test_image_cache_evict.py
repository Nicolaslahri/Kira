"""Image-proxy cache LRU eviction — keeps `.cache/images/` from filling the disk."""
from __future__ import annotations

import os

import kira.api.images as images


def test_evict_lru_trims_oldest_until_under_target(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(images, "_CACHE_MAX_BYTES", 300)
    monkeypatch.setattr(images, "_CACHE_TARGET_BYTES", 200)
    for i in range(5):                          # 5 × 100B = 500B > 300B cap
        p = tmp_path / f"img{i}.jpg"
        p.write_bytes(b"x" * 100)
        os.utime(p, (1000 + i, 1000 + i))       # img0 oldest … img4 newest
    images._evict_lru()
    survivors = {p.name for p in tmp_path.glob("*.jpg")}
    total = sum(p.stat().st_size for p in tmp_path.glob("*.jpg"))
    assert total <= 200                          # trimmed under target
    assert "img4.jpg" in survivors and "img0.jpg" not in survivors  # LRU order


def test_evict_noop_under_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(images, "_CACHE_MAX_BYTES", 10_000)
    (tmp_path / "a.jpg").write_bytes(b"x" * 100)
    images._evict_lru()
    assert (tmp_path / "a.jpg").exists()         # nothing evicted under cap
