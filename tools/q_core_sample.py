#!/usr/bin/env python3
"""q_core_sample.py -- oxidative probe of M2DG's memory-map judge.

Pokes a 0x1000-stride addr into the ?V ST field and MEASURES the deck's
answer without any assumption-layer:
  no env vars, no helper files -- raw bytes through one shell session.

Usage: .venv/bin/python tools/q_core_sample.py <addr-hex>
"""
