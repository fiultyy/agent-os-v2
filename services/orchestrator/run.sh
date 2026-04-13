#!/bin/bash
# 使用系统 python3 避免 uv/miniconda sqlite3 动态库冲突
# 如遇 "undefined symbol: sqlite3_deserialize" 错误，请使用此脚本启动
cd "$(dirname "$0")"
python3 -m src.main "$@"
