"""contracts/ 의 provide 약속 사본(원본은 hermes-controller)이 이 프로그램의 실제 입출력과 같은지 시험한다.

examples/provide-*.json 은 test_demo 가 데모 볼트로 다시 만들어 확인한다. 사본이 그것과 같으면,
데모 볼트에 사본의 요청을 주었을 때 사본의 응답이 나온다는 뜻이다.
"""

import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class ContractCopyTest(unittest.TestCase):
    def test_provide_contract_matches_the_demo_output(self):
        for name in ("provide-request", "provide-response"):
            copy = json.loads((ROOT / "contracts" / f"{name}.v1.json").read_text(encoding="utf-8"))
            example = json.loads((ROOT / "examples" / f"{name}.json").read_text(encoding="utf-8"))
            self.assertEqual(copy, example, name)
            self.assertEqual(copy["version"], 1)


if __name__ == "__main__":
    unittest.main()
