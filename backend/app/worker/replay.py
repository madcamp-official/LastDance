from typing import List, Optional

from app.schema.analysis import EditOp
from app.worker.buffer import OriginBuffer, byte_and_point


class ReplayEngine:
    """이벤트 재생 + 증분 AST 유지 (dev-plan §4.1 Step 1).

    세션 전체에서 AST는 하나만 유지하고, 편집마다 tree.edit → 증분 재파싱한다.
    parser가 None이면 버퍼 재생만 수행한다 (timing_only).
    """

    def __init__(self, parser=None) -> None:
        self.parser = parser
        self.buffer = OriginBuffer()
        self.tree = None
        if parser is not None:
            self.tree = parser.parse(b"")

    def apply(self, ev: EditOp, event_index: int) -> None:
        text_before = self.buffer.text()

        if ev.op == 0:  # insert
            start_char = ev.pos
            old_end_char = ev.pos
            self.buffer.insert(ev.pos, ev.txt, event_index)
            new_end_char = ev.pos + len(ev.txt)
        else:  # delete
            start_char = ev.pos
            deleted = self.buffer.delete(ev.pos, ev.len)
            old_end_char = ev.pos + len(deleted)
            new_end_char = ev.pos

        if self.parser is None:
            return

        start_b, start_p = byte_and_point(text_before, start_char)
        old_end_b, old_end_p = byte_and_point(text_before, old_end_char)
        text_after = self.buffer.text()
        new_end_b, new_end_p = byte_and_point(text_after, new_end_char)

        self.tree.edit(
            start_byte=start_b,
            old_end_byte=old_end_b,
            new_end_byte=new_end_b,
            start_point=start_p,
            old_end_point=old_end_p,
            new_end_point=new_end_p,
        )
        self.tree = self.parser.parse(text_after.encode("utf-8"), self.tree)

    def replay_all(self, events: List[EditOp]) -> None:
        for i, ev in enumerate(events):
            self.apply(ev, i)

    def source_bytes(self) -> bytes:
        return self.buffer.text().encode("utf-8")
