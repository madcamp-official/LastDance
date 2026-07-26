from typing import List, Tuple


class OriginBuffer:
    """문자 단위 origin(삽입 이벤트 인덱스) 태그를 유지하는 재생 버퍼.

    dev-plan §4.1 Step 6: 패턴 형성 구간 역산에는 "최종 코드의 각 문자가
    어느 이벤트에서 삽입됐는지"가 필요하다. 계획서는 piece table/rope를
    제안하지만, 세션당 K≈2,000·N≈6KB 규모에서는 리스트 splice로 충분하다.
    """

    def __init__(self) -> None:
        self._chars: List[str] = []
        self._origins: List[int] = []   # 각 문자를 삽입한 이벤트 인덱스

    def insert(self, pos: int, txt: str, event_index: int) -> None:
        pos = max(0, min(pos, len(self._chars)))
        self._chars[pos:pos] = list(txt)
        self._origins[pos:pos] = [event_index] * len(txt)

    def delete(self, pos: int, length: int) -> str:
        pos = max(0, min(pos, len(self._chars)))
        end = max(pos, min(pos + length, len(self._chars)))
        deleted = "".join(self._chars[pos:end])
        del self._chars[pos:end]
        del self._origins[pos:end]
        return deleted

    def text(self) -> str:
        return "".join(self._chars)

    def origins(self) -> List[int]:
        return self._origins

    def __len__(self) -> int:
        return len(self._chars)


def byte_and_point(text: str, char_pos: int) -> Tuple[int, Tuple[int, int]]:
    """코드포인트 오프셋 → (utf-8 바이트 오프셋, tree-sitter point).

    point의 col은 바이트 기준. O(N)이지만 이벤트당 수 회 호출로 충분히 싸다.
    """
    char_pos = max(0, min(char_pos, len(text)))
    prefix = text[:char_pos]
    byte_off = len(prefix.encode("utf-8"))
    row = prefix.count("\n")
    last_nl = prefix.rfind("\n")
    col = len(prefix[last_nl + 1 :].encode("utf-8"))
    return byte_off, (row, col)


def byte_to_char_map(text: str) -> List[int]:
    """utf-8 바이트 오프셋 → 코드포인트 오프셋 변환 테이블 (최종 코드 1회용)."""
    mapping = [0] * (len(text.encode("utf-8")) + 1)
    b = 0
    for i, ch in enumerate(text):
        w = len(ch.encode("utf-8"))
        for k in range(w):
            mapping[b + k] = i
        b += w
    mapping[b] = len(text)
    return mapping
