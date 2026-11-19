#!/usr/bin/env python3
"""examples/ 의 생성 파일을 다시 만든다. 사용법: python3 scripts/build_demo.py"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from ced_taker import demo  # noqa: E402

demo.main()
