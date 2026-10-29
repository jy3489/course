"""Immutable lazy history: appending a move must not read its predecessors."""

from collections.abc import Callable, Iterator, Sequence
from game_platform.models import Move


class MoveHistory(Sequence[Move]):
    def __init__(
        self,
        count: int,
        read: Callable[[int, int], list[Move]],
        appended: tuple[Move, ...] = (),
    ):
        self.persisted_count = count
        self._read = read
        self.appended = appended

    def __len__(self) -> int:
        return self.persisted_count + len(self.appended)

    def __getitem__(self, key):
        if isinstance(key, slice):
            start, stop, step = key.indices(len(self))
            if step != 1:
                return tuple(self[i] for i in range(start, stop, step))
            persisted = (
                self._read(start, min(stop, self.persisted_count))
                if start < self.persisted_count
                else []
            )
            return (
                tuple(persisted)
                + self.appended[
                    max(0, start - self.persisted_count) : max(
                        0, stop - self.persisted_count
                    )
                ]
            )
        if key < 0:
            key += len(self)
        if not 0 <= key < len(self):
            raise IndexError(key)
        if key >= self.persisted_count:
            return self.appended[key - self.persisted_count]
        return self._read(key, key + 1)[0]

    def __iter__(self) -> Iterator[Move]:
        for start in range(0, self.persisted_count, 100):
            yield from self._read(start, min(start + 100, self.persisted_count))
        yield from self.appended

    def append(self, move: Move) -> "MoveHistory":
        return MoveHistory(self.persisted_count, self._read, (*self.appended, move))


def append_move(history: Sequence[Move], move: Move):
    return (
        history.append(move) if isinstance(history, MoveHistory) else (*history, move)
    )
