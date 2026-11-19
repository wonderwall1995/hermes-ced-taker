"""시험이 실제 홈 폴더에 ced-taker 데이터를 만들지 못하게 지킨다.

파일 이름이 test_zz 로 시작해서 다른 시험 모듈 뒤에 실행된다. 시험 시작 전의 상태는 모듈을 불러올 때 기록한다.
(모든 시험 모듈은 시험이 실행되기 전에 먼저 불러와진다.)
"""

import os
import pathlib
import unittest


def _real_data_dir():
    home = os.environ.get("HERMES_HOME", "").strip() or "~/.hermes"
    return pathlib.Path(home).expanduser() / "ced-taker"


_EXISTED_BEFORE = _real_data_dir().exists()


class RealHomeGuardTest(unittest.TestCase):
    def test_tests_did_not_create_ced_taker_data_in_the_real_home(self):
        if _EXISTED_BEFORE:
            self.skipTest("실제 데이터 폴더가 이미 있어서 확인할 수 없다")
        self.assertFalse(_real_data_dir().exists(), "시험이 실제 홈에 ced-taker 폴더를 만들었다")


if __name__ == "__main__":
    unittest.main()
